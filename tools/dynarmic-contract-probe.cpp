// SPDX-License-Identifier: MIT
// CPU-library contract experiment only. This is not a LightTouch CPU backend.
#include <array>
#include <cstdint>
#include <cstdio>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>

#include <dynarmic/interface/A32/a32.h>
#include <dynarmic/interface/A32/coprocessor.h>

using namespace Dynarmic;
using namespace Dynarmic::A32;

struct Environment final : UserCallbacks {
    Jit* jit = nullptr;
    std::array<std::uint8_t, 4096> ram{};
    std::uint64_t ticks = 8;
    unsigned fallback = 0, svc = 0, faults = 0, mmio = 0, noexecute = 0;
    unsigned last_fallback_count = 0;
    bool deny_code = false;
    bool memory_abort = false;
    void Halt() { jit->HaltExecution(); }
    std::optional<std::uint32_t> MemoryReadCode(VAddr a) override {
        if (deny_code || a >= ram.size()) return std::nullopt;
        return Read(a, 4);
    }
    std::uint64_t Read(std::uint32_t a, unsigned width) {
        if (a == 0x1000 && width == 4) { ++mmio; return 42; }
        if (a >= ram.size() || width > ram.size() - a) {
            ++faults;
            jit->HaltExecution(memory_abort ? HaltReason::MemoryAbort : HaltReason::UserDefined1);
            return 0;
        }
        std::uint64_t v = 0;
        for (unsigned i = 0; i < width; ++i) v |= std::uint64_t(ram[a+i]) << (8*i);
        return v;
    }
    void Write(std::uint32_t a, std::uint64_t v, unsigned width) {
        if (a >= ram.size() || width > ram.size() - a) { ++faults; Halt(); return; }
        for (unsigned i = 0; i < width; ++i) ram[a+i] = std::uint8_t(v >> (8*i));
    }
    std::uint8_t MemoryRead8(VAddr a) override { return Read(a, 1); }
    std::uint16_t MemoryRead16(VAddr a) override { return Read(a, 2); }
    std::uint32_t MemoryRead32(VAddr a) override { return Read(a, 4); }
    std::uint64_t MemoryRead64(VAddr a) override { return Read(a, 8); }
    void MemoryWrite8(VAddr a, std::uint8_t v) override { Write(a,v,1); }
    void MemoryWrite16(VAddr a, std::uint16_t v) override { Write(a,v,2); }
    void MemoryWrite32(VAddr a, std::uint32_t v) override { Write(a,v,4); }
    void MemoryWrite64(VAddr a, std::uint64_t v) override { Write(a,v,8); }
    void InterpreterFallback(VAddr, std::size_t count) override {
        ++fallback; last_fallback_count = count; Halt(); // Deliberately no fake interpreter.
    }
    void CallSVC(std::uint32_t) override { ++svc; Halt(); }
    void ExceptionRaised(VAddr, Exception e) override {
        if (e == Exception::NoExecuteFault) ++noexecute;
        else ++faults;
        Halt();
    }
    void AddTicks(std::uint64_t n) override { ticks = n < ticks ? ticks - n : 0; }
    std::uint64_t GetTicksRemaining() override { return ticks; }
};

// A test coprocessor proves the callback boundary, not an ARM1176 MMU model.
struct TestCP15 final : Coprocessor {
    unsigned reads = 0, writes = 0;
    std::uint32_t control = 0;
    static std::uint64_t Read(void* arg, std::uint32_t, std::uint32_t) {
        ++static_cast<TestCP15*>(arg)->reads; return 0x410fb767;
    }
    static std::uint64_t Write(void* arg, std::uint32_t value, std::uint32_t) {
        auto& self = *static_cast<TestCP15*>(arg); ++self.writes; self.control = value; return 0;
    }
    std::optional<Callback> CompileInternalOperation(bool,unsigned,CoprocReg,CoprocReg,CoprocReg,unsigned) override { return std::nullopt; }
    CallbackOrAccessOneWord CompileSendOneWord(bool,unsigned,CoprocReg,CoprocReg,unsigned) override { return Callback{Write,this}; }
    CallbackOrAccessTwoWords CompileSendTwoWords(bool,unsigned,CoprocReg) override { return std::monostate{}; }
    CallbackOrAccessOneWord CompileGetOneWord(bool,unsigned,CoprocReg,CoprocReg,unsigned) override { return Callback{Read,this}; }
    CallbackOrAccessTwoWords CompileGetTwoWords(bool,unsigned,CoprocReg) override { return std::monostate{}; }
    std::optional<Callback> CompileLoadWords(bool,bool,CoprocReg,std::optional<std::uint8_t>) override { return std::nullopt; }
    std::optional<Callback> CompileStoreWords(bool,bool,CoprocReg,std::optional<std::uint8_t>) override { return std::nullopt; }
};

static void Require(bool yes, const std::string& name) { if (!yes) throw std::runtime_error(name); }

static void Probe(const std::string& name, std::uint32_t opcode) {
    Environment env;
    auto cp = std::make_shared<TestCP15>();
    UserConfig cfg{}; cfg.callbacks = &env; cfg.arch_version = ArchVersion::v6K;
    cfg.code_cache_size = 8 * 1024 * 1024;
    cfg.check_halt_on_memory_access = true;
    cfg.coprocessors[15] = cp;
    env.Write(0, opcode, 4);
    env.Write(4, 0xe3a02077, 4); // mov r2,#0x77: must not run after fault.
    env.Write(8, 0xeafffffe, 4); // b .
    Jit jit{cfg}; env.jit = &jit;
    jit.SetCpsr(0x13);
    jit.Regs()[0] = name == "msr-control" ? 0x12 : 0x13;
    jit.Regs()[1] = name == "data-fault" || name == "generic-halt-fault" ? 0xdead0000 : 0x1000;
    env.memory_abort = name == "data-fault";
    if (name == "noexecute") env.deny_code = true;
    const auto halt = name == "data-fault" || name == "generic-halt-fault" ? jit.Run() : jit.Step();
    bool contract = false;
    if (name == "cps" || name == "rfe" || name == "srs") contract = env.fallback == 1 && env.last_fallback_count == 1;
    if (name == "msr-control") contract = (jit.Cpsr() & 31) == 0x13;
    if (name == "cp15-read") contract = cp->reads == 1 && jit.Regs()[0] == 0x410fb767;
    if (name == "cp15-write") contract = cp->writes == 1 && cp->control == 0x13;
    if (name == "svc") contract = env.svc == 1;
    if (name == "mmio-read") contract = env.mmio == 1 && jit.Regs()[0] == 42;
    if (name == "generic-halt-fault") contract = env.faults == 1 && jit.Regs()[2] == 0x77;
    if (name == "data-fault") contract = env.faults == 1 && jit.Regs()[2] == 0 && jit.Regs()[0] == 0x13 && jit.Regs()[15] == 0 && Has(halt,HaltReason::MemoryAbort);
    if (name == "noexecute") contract = env.noexecute == 1;
    std::printf("{\"case\":\"%s\",\"contract_observed\":%s,\"r0\":%u,\"r2\":%u,\"pc\":%u,\"cpsr\":%u,\"halt_reason\":%u,\"fallback\":%u,\"faults\":%u,\"noexecute\":%u,\"mmio\":%u,\"svc\":%u,\"cp15_reads\":%u,\"cp15_writes\":%u}\n",
                name.c_str(),contract?"true":"false",jit.Regs()[0],jit.Regs()[2],jit.Regs()[15],jit.Cpsr(),static_cast<unsigned>(halt),env.fallback,env.faults,env.noexecute,env.mmio,env.svc,cp->reads,cp->writes);
    Require(contract,name);
}

int main() {
    try {
        Probe("cps", 0xf10c0080); // cpsid i
        Probe("rfe", 0xf8bd0a00); // rfeia sp!
        Probe("srs", 0xf96d0513); // srsdb sp!,#svc
        Probe("msr-control", 0xe121f000); // Requested SVC->IRQ mode change is unsupported.
        Probe("cp15-read", 0xee100f10); // mrc p15,0,r0,c0,c0,0
        Probe("cp15-write", 0xee010f10); // mcr p15,0,r0,c1,c0,0
        Probe("svc", 0xef000001);
        Probe("mmio-read", 0xe5910000); // ldr r0,[r1]
        Probe("generic-halt-fault", 0xe5910000); // Negative control: generic halt is not an abort.
        Probe("data-fault", 0xe5910000);
        Probe("noexecute", 0xe1a00000);
    } catch (const std::exception& e) {
        std::fprintf(stderr,"contract probe failed: %s\n",e.what()); return 1;
    }
}
