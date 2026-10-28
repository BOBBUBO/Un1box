package org.un1box.android

import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.URLDecoder
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import org.json.JSONException
import org.json.JSONObject

/**
 * One request as the router sees it. The body is bounded by the request's own
 * Content-Length; routes that stream an upload read [body], routes that take
 * arguments call [json] (or a query parameter) instead.
 */
internal class WebUiRequest private constructor(
    val method: String,
    val path: String,
    val query: Map<String, String>,
    val headers: Map<String, String>,
    private val stream: InputStream,
    val contentLength: Long,
) {
    /**
     * The body as a stream that ends where the request said it would: an upload route hands this to
     * the code that writes it to disk, so an IPA never sits in memory. Reading it also bounds the
     * connection to one request, which this server always does anyway.
     */
    fun bodyStream(): InputStream = Limited(stream, contentLength)

    /** Reads at most [limit] bytes of the body; shorter bodies read what is there. */
    fun readBody(limit: Long): ByteArray {
        if (contentLength <= 0) return ByteArray(0)
        val remaining = contentLength.coerceAtMost(limit)
        val buffer = ByteArrayOutputStream(minOf(remaining, 1L shl 20).toInt())
        val block = ByteArray(1 shl 16)
        var done = 0L
        while (done < remaining) {
            val n = stream.read(block, 0, minOf(block.size.toLong(), remaining - done).toInt())
            if (n < 0) break
            buffer.write(block, 0, n)
            done += n
        }
        return buffer.toByteArray()
    }

    /** The body as JSON; an empty body is an empty object. */
    fun json(): JSONObject {
        val text = readBody(1L shl 20).toString(Charsets.UTF_8)
        if (text.isBlank()) return JSONObject()
        return try { JSONObject(text) } catch (_: JSONException) { throw WebUiError(400, "The request is not valid JSON") }
    }

    companion object {
        fun parse(method: String, target: String, headers: Map<String, String>, stream: InputStream, contentLength: Long): WebUiRequest {
            val path = target.substringBefore('?')
            val query = mutableMapOf<String, String>()
            for (pair in target.substringAfter('?', "").split('&')) {
                if (pair.isEmpty()) continue
                val name = URLDecoder.decode(pair.substringBefore('='), "UTF-8")
                val value = URLDecoder.decode(pair.substringAfter('=', ""), "UTF-8")
                query[name] = value
            }
            return WebUiRequest(method, path, query, headers, stream, contentLength)
        }
    }
}

/** A response this server can write: a status, a content type and the bytes themselves. */
internal class WebUiResponse private constructor(val status: Int, val type: String, val bytes: ByteArray) {
    companion object {
        fun json(value: Any?, status: Int = 200) = WebUiResponse(status, "application/json; charset=utf-8",
            (if (value is JSONObject) value else JSONObject().put("result", value)).toString().toByteArray(Charsets.UTF_8))

        fun error(status: Int, message: String) = WebUiResponse(status, "application/json; charset=utf-8",
            JSONObject().put("error", message).toString().toByteArray(Charsets.UTF_8))

        fun bytes(type: String, data: ByteArray, status: Int = 200) = WebUiResponse(status, type, data)
    }
}

/** An error a route can throw with the status it wants, for the server to answer with. */
internal class WebUiError(val status: Int, message: String) : Exception(message)

/** Reads at most [limit] bytes from [source]: the request body as its receiver declared it. */
private class Limited(private val source: InputStream, private val limit: Long) : InputStream() {
    private var left = limit

    override fun read(): Int = if (left <= 0) -1 else source.read().also { if (it >= 0) left-- }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (left <= 0) return -1
        val n = source.read(buffer, offset, minOf(length.toLong(), left).toInt())
        if (n > 0) left -= n
        return n
    }

    override fun available(): Int = if (left <= 0) 0 else minOf(source.available(), left.toInt()).coerceAtLeast(0)

    override fun close() { left = 0; source.close() }
}

/**
 * The Web UI's own HTTP server: written for this app rather than taken from a library, because the
 * Gradle build verifies every dependency it links, and the Web UI needs none. It speaks exactly
 * what the page needs: fixed-length bodies (so uploads stream to disk instead of into memory),
 * `Expect: 100-continue` (so curl can upload an IPA), one shared secret, and no keep-alive — every
 * response closes its connection, so an idle browser never holds a server thread.
 *
 * Every connection is served on one thread of a small pool, and every route runs there: the store
 * and guest-app calls are blocking network work, which is what a request thread is for. Nothing in
 * here touches the main thread.
 */
internal class WebUiServer(private val port: Int, private val token: String, private val route: (WebUiRequest) -> WebUiResponse) {
    @Volatile private var listening: ServerSocket? = null
    private var accepter: Thread? = null
    private var pool: ExecutorService? = null

    /** Opens the port; throws (with the operating system's own message) when it cannot. */
    @Synchronized fun start() {
        check(listening == null) { "the Web UI server is already running" }
        val server = ServerSocket(port, 8, InetAddress.getByName("0.0.0.0"))
        val workers = Executors.newFixedThreadPool(CONNECTIONS) { task ->
            Thread(task, "webui-connection").apply { isDaemon = true }
        }
        listening = server
        pool = workers
        accepter = Thread({ acceptLoop(server, workers) }, "webui-accept").apply { isDaemon = true; start() }
    }

    @Synchronized fun stop() {
        val server = listening ?: return
        listening = null
        runCatching { server.close() }
        pool?.shutdownNow()
        pool = null
        accepter = null
    }

    val running get() = listening != null

    private fun acceptLoop(server: ServerSocket, workers: ExecutorService) {
        while (!server.isClosed) {
            val client = try { server.accept() } catch (_: IOException) { break }
            workers.execute {
                try { serve(client) } catch (_: Exception) { runCatching { client.close() } }
            }
        }
    }

    private fun serve(client: Socket) {
        client.use {
            client.tcpNoDelay = true
            client.soTimeout = READ_TIMEOUT_MS
            val input = BufferedInputStream(client.getInputStream(), 1 shl 16)
            val output = client.getOutputStream()
            val head = readHead(input) ?: return
            // A body is never optional in a way that matters: without a length there is none, and
            // chunked bodies are refused rather than half-read.
            if ("chunked" == head.headers["transfer-encoding"]) {
                write(output, WebUiResponse.error(411, "Send the body with a Content-Length"), true)
                return
            }
            if (head.headers["expect"]?.contains("100-continue") == true)
                output.write("HTTP/1.1 100 Continue\r\n\r\n".toByteArray(Charsets.ISO_8859_1))
            val supplied = head.query["token"] ?: head.headers["x-webui-token"]
            if (supplied != token) {
                write(output, WebUiResponse.error(403, "This Web UI needs its token; open the address shown in Un1box's Settings"), false)
                return
            }
            val response = try {
                route(head.request)
            } catch (failure: WebUiError) {
                WebUiResponse.error(failure.status, failure.message ?: "Request failed")
            } catch (failure: Exception) {
                android.util.Log.w("Un1box", "Web UI request failed", failure)
                WebUiResponse.error(500, failure.message ?: "Request failed")
            }
            write(output, response, head.method == "HEAD")
        }
    }

    private class Head(val request: WebUiRequest, val headers: Map<String, String>)

    private fun readHead(input: InputStream): Head? {
        val requestLine = readLine(input, 8 shl 10) ?: return null
        val parts = requestLine.split(' ')
        if (parts.size != 3 || !parts[2].startsWith("HTTP/1.")) throw WebUiError(400, "Only HTTP/1.x is supported")
        val headers = mutableMapOf<String, String>()
        var lines = 0
        while (true) {
            val line = readLine(input, 8 shl 10) ?: return null
            if (line.isEmpty()) break
            if (++lines > 100) throw WebUiError(431, "Too many headers")
            val name = line.substringBefore(':').trim().lowercase(Locale.ROOT)
            if (name.isEmpty()) continue
            headers[name] = line.substringAfter(':', "").trim()
        }
        if (headers.size > 100) throw WebUiError(431, "Too many headers")
        val length = headers["content-length"]?.toLongOrNull() ?: 0L
        if (length < 0 || length > MAX_BODY) throw WebUiError(413, "The body is too large for this server")
        return Head(WebUiRequest.parse(parts[0], parts[1], headers, input, length), headers)
    }

    /** One CRLF-terminated line, bytes as ISO-8859-1 so nothing but the line itself is interpreted. */
    private fun readLine(input: InputStream, limit: Int): String? {
        val line = ByteArrayOutputStream()
        while (line.size() <= limit) {
            val byte = try { input.read() } catch (_: SocketTimeoutException) { return null }
            if (byte < 0) return if (line.size() == 0) null else null
            if (byte == '\n'.code) break
            line.write(byte)
        }
        if (line.size() > limit) throw WebUiError(431, "A request line is too long")
        var text = line.toString(Charsets.ISO_8859_1)
        if (text.endsWith("\r")) text = text.dropLast(1)
        return text
    }

    private fun write(output: OutputStream, response: WebUiResponse, headOnly: Boolean) {
        val reason = when (response.status) {
            200 -> "OK"; 400 -> "Bad Request"; 403 -> "Forbidden"; 404 -> "Not Found"; 405 -> "Method Not Allowed"
            411 -> "Length Required"; 413 -> "Payload Too Large"; 431 -> "Request Header Fields Too Large"; 500 -> "Internal Server Error"
            else -> "Status"
        }
        val head = StringBuilder()
        head.append("HTTP/1.1 ").append(response.status).append(' ').append(reason).append("\r\n")
        head.append("Content-Type: ").append(response.type).append("\r\n")
        head.append("Content-Length: ").append(response.bytes.size).append("\r\n")
        head.append("Cache-Control: no-store\r\n")
        head.append("X-Content-Type-Options: nosniff\r\n")
        head.append("Referrer-Policy: no-referrer\r\n")
        if (response.type.startsWith("text/html"))
            head.append("Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; ")
                .append("img-src 'self' https: data:; connect-src 'self'\r\n")
        head.append("Connection: close\r\n\r\n")
        output.write(head.toString().toByteArray(Charsets.ISO_8859_1))
        if (!headOnly) {
            output.write(response.bytes)
            output.flush()
        }
    }

    companion object {
        /** A browser keeps a connection per origin in flight; two clients stay within this. */
        private const val CONNECTIONS = 8
        private const val READ_TIMEOUT_MS = 30_000
        /** IPAs run to hundreds of megabytes and IPSWs close to one; nothing legitimate is larger. */
        private const val MAX_BODY = 4L shl 30
    }
}
