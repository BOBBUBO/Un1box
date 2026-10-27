import HostRuntime
import Testing

@testable import LightTouchCore

struct BootBudgetTests {
    /// An Intel Mac's boot gets the budget its slower emulation needs; Apple silicon keeps iPod 240 s, iPad 300 s.
    @Test func bootBudgetsScaleByTheHostSlowdown() {
        #if arch(x86_64)
            let slowdown = 5.0
        #else
            let slowdown = 1.0
        #endif
        #expect(Board.hostSlowdown == slowdown)
        #expect(Board.n72.bootBudget == 240 * slowdown && Board.k48.bootBudget == 300 * slowdown)
    }
}
