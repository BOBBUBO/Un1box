// SPDX-License-Identifier: MIT
// Actual DeviceRuntime library; no Testing/XCTest/Xcode, helper or guest.
import DeviceRuntime
import Foundation

final class Replies: @unchecked Sendable {
    let lock = NSLock()
    var count = 0
    var failures = 0
    func record(_ result: Result<LinkReply, DeviceLinkError>) {
        lock.lock()
        defer { lock.unlock() }
        count += 1
        if case .failure(.closed("not connected")) = result {} else { failures += 1 }
    }
}
let queue = DispatchQueue(label: "org.lighttouch.native-test.reply")
let link = DeviceLink(configuration: .init(instance: UUID()), queue: queue)
let replies = Replies()
let group = DispatchGroup()
DispatchQueue.concurrentPerform(iterations: 256) { _ in
    group.enter()
    link.request(.snapshotStatus, timeout: 0.01) { result in
        dispatchPrecondition(condition: .onQueue(queue))
        replies.record(result)
        group.leave()
    }
}
precondition(group.wait(timeout: .now() + 5) == .success, "missing bounded closed-link reply")
precondition(replies.count == 256 && replies.failures == 0, "reply cardinality or error contract")
precondition(link.pid == 0, "test must not start a helper")
if case .some = link.info { fatalError("unconnected link has helper info") }
if case .some = link.status { fatalError("unconnected link has shared status") }
if case .some = link.frontSurface() { fatalError("unconnected link has a surface") }
print("PASS actual DeviceLink 256 concurrent pre-start requests: one closed reply each on callback queue; no helper/guest")
