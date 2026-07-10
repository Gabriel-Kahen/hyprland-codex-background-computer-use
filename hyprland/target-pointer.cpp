#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/view/WLSurface.hpp>
#include <hyprland/src/managers/PointerManager.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/managers/SessionLockManager.hpp>
#include <hyprland/src/managers/input/InputManager.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/protocols/core/DataDevice.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <format>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

inline HANDLE PHANDLE = nullptr;

namespace {

struct ParsedRequest {
    std::string action;
    uintptr_t   address = 0;
    double      x1 = 0;
    double      y1 = 0;
    double      x2 = 0;
    double      y2 = 0;
    std::string button = "left";
    int         amount = 1;
};

std::string jsonError(const std::string& message) {
    std::string escaped;
    escaped.reserve(message.size());
    for (const char ch : message) {
        if (ch == '"' || ch == '\\') escaped.push_back('\\');
        if (ch == '\n') escaped += "\\n";
        else escaped.push_back(ch);
    }
    return std::format("{{\"ok\":false,\"error\":\"{}\"}}", escaped);
}

std::string jsonOk(const ParsedRequest& req, const Vector2D& local, const std::string& surfaceKind) {
    return std::format(
        "{{\"ok\":true,\"action\":\"{}\",\"address\":\"0x{:x}\",\"local_x\":{:.3f},\"local_y\":{:.3f},\"surface\":\"{}\",\"cursor_moved\":false,\"keyboard_focus_changed\":false}}",
        req.action, req.address, local.x, local.y, surfaceKind);
}

bool parseAddress(const std::string& value, uintptr_t& output) {
    auto text = value;
    if (text.starts_with("0x") || text.starts_with("0X")) text.erase(0, 2);
    if (text.empty()) return false;
    const auto [ptr, ec] = std::from_chars(text.data(), text.data() + text.size(), output, 16);
    return ec == std::errc{} && ptr == text.data() + text.size() && output != 0;
}

bool parseDouble(const std::string& value, double& output) {
    try {
        size_t consumed = 0;
        output = std::stod(value, &consumed);
        return consumed == value.size() && std::isfinite(output);
    } catch (...) { return false; }
}

bool parseInt(const std::string& value, int& output) {
    const auto [ptr, ec] = std::from_chars(value.data(), value.data() + value.size(), output);
    return ec == std::errc{} && ptr == value.data() + value.size();
}

std::vector<std::string> words(const std::string& request) {
    std::istringstream stream(request);
    std::vector<std::string> values;
    for (std::string value; stream >> value;) values.push_back(value);
    return values;
}

bool parseRequest(const std::string& request, ParsedRequest& parsed, std::string& error) {
    auto args = words(request);
    if (!args.empty() && args.front() == "cutarget") args.erase(args.begin());
    if (args.size() < 4) {
        error = "usage: cutarget click|scroll|drag ADDRESS X Y [options]";
        return false;
    }
    parsed.action = args[0];
    if (!parseAddress(args[1], parsed.address) || !parseDouble(args[2], parsed.x1) || !parseDouble(args[3], parsed.y1)) {
        error = "invalid address or coordinate";
        return false;
    }
    if (parsed.action == "click") {
        if (args.size() > 4) parsed.button = args[4];
        if (args.size() > 5 && !parseInt(args[5], parsed.amount)) {
            error = "invalid click count";
            return false;
        }
        if (args.size() > 6 || parsed.amount < 1 || parsed.amount > 3 ||
            (parsed.button != "left" && parsed.button != "right" && parsed.button != "middle")) {
            error = "click expects ADDRESS X Y [left|right|middle] [1..3]";
            return false;
        }
    } else if (parsed.action == "scroll") {
        if (args.size() != 5 || !parseInt(args[4], parsed.amount) || parsed.amount == 0 || std::abs(parsed.amount) > 20) {
            error = "scroll expects ADDRESS X Y STEPS (-20..20, excluding 0)";
            return false;
        }
    } else if (parsed.action == "drag") {
        if (args.size() < 6 || args.size() > 8 || !parseDouble(args[4], parsed.x2) || !parseDouble(args[5], parsed.y2)) {
            error = "drag expects ADDRESS START_X START_Y END_X END_Y [left|right|middle] [2..32 motion steps]";
            return false;
        }
        if (args.size() > 6) parsed.button = args[6];
        parsed.amount = 8;
        if (args.size() > 7 && !parseInt(args[7], parsed.amount)) {
            error = "invalid drag motion step count";
            return false;
        }
        if (parsed.amount < 2 || parsed.amount > 32 ||
            (parsed.button != "left" && parsed.button != "right" && parsed.button != "middle")) {
            error = "invalid drag button or motion step count";
            return false;
        }
    } else {
        error = "unknown action; expected click, scroll, or drag";
        return false;
    }
    return true;
}

PHLWINDOW findWindow(uintptr_t address) {
    for (const auto& window : g_pCompositor->m_windows) {
        if (window && reinterpret_cast<uintptr_t>(window.get()) == address) return window;
    }
    return nullptr;
}

uint32_t buttonCode(const std::string& button) {
    if (button == "right") return 273;
    if (button == "middle") return 274;
    return 272;
}

Vector2D localForSurface(SP<CWLSurfaceResource> surface, const Vector2D& cursor) {
    if (!surface) return {};
    if (const auto window = g_pCompositor->getWindowFromSurface(surface))
        return g_pCompositor->vectorToSurfaceLocal(cursor, window, surface);
    const auto wrapper = Desktop::View::CWLSurface::fromResource(surface);
    if (!wrapper) return {};
    const auto box = wrapper->getSurfaceBoxGlobal();
    if (!box) return {};
    return cursor - Vector2D{box->x, box->y};
}

class PointerFocusRestore {
  public:
    PointerFocusRestore() : surface(g_pSeatManager->m_state.pointerFocus.lock()), cursor(g_pPointerManager->position()), local(localForSurface(surface, cursor)) {}
    ~PointerFocusRestore() {
        const auto now = static_cast<uint32_t>(Time::millis(Time::steadyNow()));
        g_pSeatManager->setPointerFocus(surface, local);
        if (surface) {
            g_pSeatManager->sendPointerMotion(now, local);
            g_pSeatManager->sendPointerFrame();
        }
    }

  private:
    SP<CWLSurfaceResource> surface;
    Vector2D               cursor;
    Vector2D               local;
};

struct TargetPoint {
    SP<CWLSurfaceResource> surface;
    Vector2D               local;
    std::string            kind;
};

TargetPoint resolveTarget(PHLWINDOW window, double x, double y, bool forceMain = false) {
    const auto size = window->m_realSize->goal();
    if (x < 0 || y < 0 || x >= size.x || y >= size.y)
        throw std::runtime_error(std::format("coordinate ({:.1f},{:.1f}) is outside window size {:.1f}x{:.1f}", x, y, size.x, size.y));

    const Vector2D local{x, y};
    if (window->m_isX11 || forceMain)
        return {window->wlSurface()->resource(), local, window->m_isX11 ? "xwayland" : "main"};

    const auto global = window->m_realPosition->goal() + local;
    Vector2D surfaceLocal;
    auto surface = g_pCompositor->vectorWindowToSurface(global, window, surfaceLocal);
    if (!surface) throw std::runtime_error("no input surface exists at the requested window coordinate");
    return {surface, surfaceLocal, surface == window->wlSurface()->resource() ? "main" : "subsurface"};
}

void ensureSafeToInject(PHLWINDOW window) {
    if (!window || !Desktop::View::validMapped(window)) throw std::runtime_error("target window is not mapped");
    if (!window->acceptsInput()) throw std::runtime_error("target window does not currently accept input");
    if (!g_pSeatManager || !g_pSeatManager->m_mouse) throw std::runtime_error("Hyprland has no active pointer seat");
    if (g_pSessionLockManager && g_pSessionLockManager->isSessionLocked()) throw std::runtime_error("session is locked");
    if (g_pInputManager->hasHeldButtons()) throw std::runtime_error("physical pointer button is currently held");
    if (g_pInputManager->isConstrained() || g_pInputManager->isLocked()) throw std::runtime_error("physical pointer is constrained or locked");
    if (PROTO::data && PROTO::data->dndActive()) throw std::runtime_error("a drag-and-drop operation is active");
}

std::string handleRequest(eHyprCtlOutputFormat, std::string request) {
    try {
        ParsedRequest parsed;
        std::string error;
        if (!parseRequest(request, parsed, error)) return jsonError(error);
        const auto window = findWindow(parsed.address);
        ensureSafeToInject(window);

        if (window->m_isX11) throw std::runtime_error("XWayland targets must use the same-session broker's XTEST route");

        PointerFocusRestore restore;
        const auto now = static_cast<uint32_t>(Time::millis(Time::steadyNow()));
        auto start = resolveTarget(window, parsed.x1, parsed.y1, parsed.action == "drag");
        g_pSeatManager->setPointerFocus(start.surface, start.local);
        g_pSeatManager->sendPointerMotion(now, start.local);
        g_pSeatManager->sendPointerFrame();

        if (parsed.action == "click") {
            const auto button = buttonCode(parsed.button);
            for (int i = 0; i < parsed.amount; ++i) {
                g_pSeatManager->sendPointerButton(now, button, WL_POINTER_BUTTON_STATE_PRESSED);
                g_pSeatManager->sendPointerFrame();
                g_pSeatManager->sendPointerButton(now, button, WL_POINTER_BUTTON_STATE_RELEASED);
                g_pSeatManager->sendPointerFrame();
            }
        } else if (parsed.action == "scroll") {
            const auto value120 = parsed.amount * 120;
            g_pSeatManager->sendPointerAxis(now, WL_POINTER_AXIS_VERTICAL_SCROLL, static_cast<double>(parsed.amount) * 15.0,
                                            parsed.amount, value120, WL_POINTER_AXIS_SOURCE_WHEEL,
                                            WL_POINTER_AXIS_RELATIVE_DIRECTION_IDENTICAL);
            g_pSeatManager->sendPointerFrame();
        } else if (parsed.action == "drag") {
            const auto button = buttonCode(parsed.button);
            g_pSeatManager->sendPointerButton(now, button, WL_POINTER_BUTTON_STATE_PRESSED);
            g_pSeatManager->sendPointerFrame();
            for (int i = 1; i <= parsed.amount; ++i) {
                const double t = static_cast<double>(i) / parsed.amount;
                const Vector2D local{parsed.x1 + (parsed.x2 - parsed.x1) * t, parsed.y1 + (parsed.y2 - parsed.y1) * t};
                const auto size = window->m_realSize->goal();
                if (local.x < 0 || local.y < 0 || local.x >= size.x || local.y >= size.y)
                    throw std::runtime_error("drag endpoint or path leaves the target window");
                g_pSeatManager->sendPointerMotion(now, local);
                g_pSeatManager->sendPointerFrame();
            }
            g_pSeatManager->sendPointerButton(now, button, WL_POINTER_BUTTON_STATE_RELEASED);
            g_pSeatManager->sendPointerFrame();
        }

        return jsonOk(parsed, start.local, start.kind);
    } catch (const std::exception& exception) { return jsonError(exception.what()); }
}

} // namespace

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    PHANDLE = handle;
    if (std::string{__hyprland_api_get_hash()} != std::string{__hyprland_api_get_client_hash()})
        throw std::runtime_error("same-session-target-pointer: Hyprland header/runtime version mismatch");

    const auto command = HyprlandAPI::registerHyprCtlCommand(
        PHANDLE, SHyprCtlCommand{.name = "cutarget", .exact = false, .fn = handleRequest});
    if (!command) throw std::runtime_error("same-session-target-pointer: failed to register cutarget command");

    return {"same-session-target-pointer", "Atomic window-targeted pointer events without cursor movement", "Gabe", "0.1.0"};
}

APICALL EXPORT void PLUGIN_EXIT() {}
