/* SPDX-License-Identifier: MIT
 * Native macOS CGL capability smoke, no guest/firmware/window. This does not
 * qualify the production renderer or claim that the guest uses Metal directly.
 * clang++ -std=c++17 -Wall -Wextra -Werror -Wno-deprecated-declarations \
 *   macOS/tests/CGLReadback.cpp -framework OpenGL -o cgl-readback
 */
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <OpenGL/glext.h>
#include <cassert>
#include <cstdio>
#include <cstring>

int main()
{
    CGLPixelFormatAttribute attributes[] = {
        kCGLPFAAccelerated, kCGLPFAColorSize, (CGLPixelFormatAttribute)24,
        kCGLPFAAlphaSize, (CGLPixelFormatAttribute)8,
        kCGLPFADepthSize, (CGLPixelFormatAttribute)16, (CGLPixelFormatAttribute)0
    };
    CGLPixelFormatObj format = nullptr;
    GLint count = 0;
    assert(CGLChoosePixelFormat(attributes, &format, &count) == kCGLNoError && count > 0);
    CGLContextObj context = nullptr;
    assert(CGLCreateContext(format, nullptr, &context) == kCGLNoError);
    CGLDestroyPixelFormat(format);
    assert(CGLSetCurrentContext(context) == kCGLNoError);
    std::printf("CGL renderer=%s version=%s\n", glGetString(GL_RENDERER), glGetString(GL_VERSION));
    const char *extensions = reinterpret_cast<const char *>(glGetString(GL_EXTENSIONS));
    assert(extensions && std::strstr(extensions, "GL_EXT_framebuffer_object"));
    GLuint framebuffer = 0, texture = 0, depth = 0;
    glGenFramebuffersEXT(1, &framebuffer);
    glBindFramebufferEXT(GL_FRAMEBUFFER_EXT, framebuffer);
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 320, 480, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
    glFramebufferTexture2DEXT(GL_FRAMEBUFFER_EXT, GL_COLOR_ATTACHMENT0_EXT, GL_TEXTURE_2D, texture, 0);
    glGenRenderbuffersEXT(1, &depth);
    glBindRenderbufferEXT(GL_RENDERBUFFER_EXT, depth);
    glRenderbufferStorageEXT(GL_RENDERBUFFER_EXT, GL_DEPTH24_STENCIL8_EXT, 320, 480);
    glFramebufferRenderbufferEXT(GL_FRAMEBUFFER_EXT, GL_DEPTH_ATTACHMENT_EXT, GL_RENDERBUFFER_EXT, depth);
    glFramebufferRenderbufferEXT(GL_FRAMEBUFFER_EXT, GL_STENCIL_ATTACHMENT_EXT, GL_RENDERBUFFER_EXT, depth);
    assert(glCheckFramebufferStatusEXT(GL_FRAMEBUFFER_EXT) == GL_FRAMEBUFFER_COMPLETE_EXT);
    glViewport(0, 0, 320, 480);
    glClearColor(0.25f, 0.5f, 0.75f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT | GL_STENCIL_BUFFER_BIT);
    unsigned char pixel[4]{};
    glReadPixels(160, 240, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
    assert(pixel[0] >= 63 && pixel[0] <= 65 && pixel[1] >= 127 && pixel[1] <= 129);
    assert(pixel[2] >= 190 && pixel[2] <= 192 && pixel[3] == 255);
    glColor4ub(255, 0, 0, 255);
    glBegin(GL_TRIANGLES);
    glVertex2f(-1, -1); glVertex2f(1, -1); glVertex2f(0, 1);
    glEnd();
    glReadPixels(160, 240, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
    assert(pixel[0] == 255 && pixel[1] == 0 && pixel[2] == 0 && pixel[3] == 255);
    assert(glGetError() == GL_NO_ERROR);
    glBindFramebufferEXT(GL_FRAMEBUFFER_EXT, 0);
    glDeleteFramebuffersEXT(1, &framebuffer);
    glDeleteTextures(1, &texture);
    glDeleteRenderbuffersEXT(1, &depth);
    assert(CGLSetCurrentContext(nullptr) == kCGLNoError);
    CGLReleaseContext(context);
    std::puts("PASS accelerated CGL legacy GL, production-size D24S8 FBO and fixed-function readback; host only");
}
