#include "../ReplayQueue.hpp"
#include <cassert>
#include <string>
#include <vector>

struct Event {
    double at;
    std::string name;
    bool mutation, replay = false;
};

int main() {
    std::vector<Event> script{{1, "guest-launch", true}, {1, "touch", true},      {1, "button", true},
                              {1, "pause", true},        {1, "guest-type", true}, {1, "screenshot", false},
                              {1, "cancel", false},      {2, "future", true}};
    size_t next = 0;
    bool stopping = false, interrupted = true;
    int stops = 0;
    std::vector<std::string> delivered;
    auto stop = [&] {
        if (!stopping) {
            stopping = true;
            ++stops;
            delivered.push_back("stop");
        }
    };
    auto admit = [&](const Event &e) { return !stopping || !e.mutation; };
    auto dispatch = [&](const Event &e) { delivered.push_back(e.name); };
    // A signal already set when multiple RPC/input commands are overdue must
    // reach Stop first, while observational checkpoints remain available.
    lt::dispatch_due_script(script, next, 1, [&] { return interrupted; }, stop, admit, dispatch);
    assert((delivered == std::vector<std::string>{"stop", "screenshot", "cancel"}));
    assert(next == 7 && stops == 1);
    interrupted = false;
    lt::dispatch_due_script(script, next, 2, [&] { return interrupted; }, stop, admit, dispatch);
    assert(next == script.size() && delivered.size() == 3);

    // A signal arising during a batch is observed before its next command;
    // Stop may erase future generated replay, changing the current queue entry.
    script = {{1, "first", true}, {1, "generated", true, true}, {1, "checkpoint", false}, {1, "type", true}};
    next = 0;
    stopping = interrupted = false;
    delivered.clear();
    auto erase_stop = [&] {
        stop();
        lt::discard_replay(script, next);
    };
    lt::dispatch_due_script(
        script, next, 1, [&] { return interrupted; }, erase_stop, admit,
        [&](const Event &e) {
            delivered.push_back(e.name);
            interrupted = true;
        });
    assert((delivered == std::vector<std::string>{"first", "stop", "checkpoint"}));
    assert(next == script.size());

    // Scripted Stop in the same batch also rejects later mutations; a new
    // independent dispatch state must still permit ordinary input.
    script = {{1, "stop", false}, {1, "type", true}, {1, "checkpoint", false}};
    next = 0;
    stopping = interrupted = false;
    delivered.clear();
    lt::dispatch_due_script(
        script, next, 1, [] { return false; }, stop, admit,
        [&](const Event &e) {
            if (e.name == "stop")
                stop();
            else
                dispatch(e);
        });
    assert((delivered == std::vector<std::string>{"stop", "checkpoint"}));
    script = {{1, "fresh-input", true}};
    next = 0;
    stopping = false;
    lt::dispatch_due_script(script, next, 1, [] { return false; }, stop, admit, dispatch);
    assert(delivered.back() == "fresh-input");
}
