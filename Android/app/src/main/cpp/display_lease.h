#pragma once
#include <algorithm>
#include <cstdint>
#include <limits>
#include <vector>
#include <utility>

// Adopt the reference returned by platform conversion. Declare outside the
// guarded scope so rejection/destruction releases outside the control guard.
template<class Window, void (*Release)(Window *)>
class ConvertedWindow {
public:
    explicit ConvertedWindow(Window *value):value_(value) {}
    ~ConvertedWindow() { if(value_)Release(value_); }
    ConvertedWindow(const ConvertedWindow &)=delete;
    ConvertedWindow &operator=(const ConvertedWindow &)=delete;
    Window *get() const { return value_; }
    Window *take() { return std::exchange(value_,nullptr); }
private:
    Window *value_;
};

// Caller holds the native control guard. Tokens belong to one worker process;
// the View also retains that worker's Binder identity, never just this number.
class DisplayLease {
public:
    struct Request { uint64_t owner=0, order=0; };
    uint64_t claim() {
        if(serial_==uint64_t(std::numeric_limits<int64_t>::max()))return 0;
        owner_=++serial_;++order_;contacts_.clear();return owner_;
    }
    bool owns(uint64_t token) const { return token && token==owner_; }
    Request begin(uint64_t token) { return owns(token)?Request{token,++order_}:Request{}; }
    bool current(Request request) const { return owns(request.owner) && request.order==order_; }
    bool detach(uint64_t token) {
        if(!owns(token))return false;
        ++order_;contacts_.clear();return true; // Activity authority survives.
    }
    bool release(uint64_t token) {
        if(!owns(token))return false;
        owner_=0;++order_;contacts_.clear();return true;
    }
    bool contactsHeld() const { return !contacts_.empty(); }
    void contactsReleased() { contacts_.clear(); input_pending_=false; }
    void submittedInput(uint64_t request,uint64_t owner,bool release_needed) {
        input_request_=request;input_owner_=owner;input_release_needed_=release_needed;
        input_pending_=request!=0;
    }
    bool reportsRejectedInput(uint64_t request) const {
        return input_pending_ && request==input_request_ && (input_owner_==0 || owns(input_owner_));
    }
    bool cancelsRejectedInput(uint64_t request) const {
        return reportsRejectedInput(request) && (input_owner_==0 || input_release_needed_);
    }
    void inputCompleted(uint64_t request) { if(request==input_request_)input_pending_=false; }
    void admittedContact(int32_t id,int32_t phase) {
        auto existing=std::find(contacts_.begin(),contacts_.end(),id);
        // Successfully admitted public UP (2) and CANCEL (3) both release IDs.
        if(phase>=2) { if(existing!=contacts_.end())contacts_.erase(existing); }
        else if(existing==contacts_.end())contacts_.push_back(id);
    }
private:
    uint64_t serial_=0,owner_=0,order_=0;
    uint64_t input_request_=0,input_owner_=0;
    bool input_pending_=false,input_release_needed_=false;
    std::vector<int32_t> contacts_;
};
