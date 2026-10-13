/**
 * android-adapter.js
 * ───────────────────
 * Native bridge and mobile UX enhancements for QuantEdge IntraBot Android App.
 * Includes:
 * 1. Mobile Top Header with Hamburger Button & Brand
 * 2. Sliding Glassmorphic Hamburger Drawer with all features
 * 3. Spacious 5-Tab Bottom Navigation Bar
 * 4. Native Haptics, Hardware Back Handling, and Server URL Management
 */

(function () {
    "use strict";

    console.log("[AndroidAdapter] Initializing IntraBot Mobile UX Adapter...");

    // Flag to verify native Android environment
    const isAndroid = !!(window.AndroidBridge && typeof window.AndroidBridge.isNativeAndroid === 'function');
    document.documentElement.classList.add("is-android-app");
    if (document.body) document.body.classList.add("is-android-app");

    // ── Haptic Feedback Helpers ──────────────────────────────────────────────
    function haptic(type = "light") {
        try {
            if (isAndroid && window.AndroidBridge.playHaptic) {
                window.AndroidBridge.playHaptic(type);
            } else if (navigator.vibrate) {
                navigator.vibrate(type === "heavy" ? 80 : 30);
            }
        } catch (e) {}
    }

    function showToast(msg) {
        try {
            if (isAndroid && window.AndroidBridge.showToast) {
                window.AndroidBridge.showToast(msg);
            } else {
                console.log("[Toast]", msg);
            }
        } catch (e) {}
    }

    // Attach haptics to clicks
    document.addEventListener("click", function (e) {
        const target = e.target.closest("button, .btn-mode, .btn-toggle, .btn-danger, .mob-nav-item, .drawer-nav-item");
        if (!target) return;
        if (target.classList.contains("btn-danger") || target.innerText.includes("HALT")) {
            haptic("heavy");
        } else if (target.classList.contains("btn-toggle") || target.innerText.includes("START")) {
            haptic("success");
        } else {
            haptic("light");
        }
    }, true);

    // ── Hardware Back Button Handling ─────────────────────────────────────────
    window.handleAndroidBack = function () {
        // 1. Drawer open? Close it!
        if (isDrawerOpen()) {
            closeDrawer();
            haptic("light");
            return true;
        }

        // 2. History modal open?
        const histScreen = document.getElementById("history-screen");
        if (histScreen && (histScreen.style.display === "block" || histScreen.classList.contains("active") || getComputedStyle(histScreen).display !== "none")) {
            if (typeof toggleHistoryScreen === "function") {
                toggleHistoryScreen();
                haptic("light");
                return true;
            }
        }

        // 3. Config modal open?
        const configModal = document.getElementById("config-modal");
        if (configModal && configModal.style.display !== "none") {
            if (typeof closeConfig === "function") {
                closeConfig();
                haptic("light");
                return true;
            }
        }

        // 4. Server modal open?
        const serverModal = document.getElementById("mobile-server-modal");
        if (serverModal && serverModal.style.display !== "none") {
            serverModal.style.display = "none";
            haptic("light");
            return true;
        }

        return false; // Delegate to MainActivity
    };

    // ── Hamburger Drawer Implementation ───────────────────────────────────────
    function isDrawerOpen() {
        const drawer = document.getElementById("android-hamburger-drawer");
        return drawer && drawer.classList.contains("open");
    }

    window.toggleHamburgerDrawer = function () {
        haptic("light");
        if (isDrawerOpen()) {
            closeDrawer();
        } else {
            openDrawer();
        }
    };

    function openDrawer() {
        const drawer = document.getElementById("android-hamburger-drawer");
        const backdrop = document.getElementById("android-drawer-backdrop");
        if (drawer && backdrop) {
            drawer.classList.add("open");
            backdrop.classList.add("visible");
            document.body.style.overflow = "hidden";
        }
    }

    function closeDrawer() {
        const drawer = document.getElementById("android-hamburger-drawer");
        const backdrop = document.getElementById("android-drawer-backdrop");
        if (drawer && backdrop) {
            drawer.classList.remove("open");
            backdrop.classList.remove("visible");
            document.body.style.overflow = "";
        }
    }

    function setupHamburgerDrawer() {
        if (document.getElementById("android-hamburger-drawer")) return;

        // 1. Backdrop
        const backdrop = document.createElement("div");
        backdrop.id = "android-drawer-backdrop";
        backdrop.onclick = closeDrawer;
        document.body.appendChild(backdrop);

        // Current page detection
        const path = window.location.pathname;
        const isDual = path.endsWith("dual.html") || path.endsWith("/dual");
        const isCreds = path.endsWith("credentials.html") || path.endsWith("/credentials");
        const isHome = !isDual && !isCreds;

        // 2. Drawer Panel
        const drawer = document.createElement("aside");
        drawer.id = "android-hamburger-drawer";
        drawer.innerHTML = `
            <div class="drawer-header">
                <div class="drawer-brand">
                    <div class="drawer-logo-wrap">
                        <svg viewBox="0 0 64 64" width="32" height="32" fill="none">
                            <circle cx="32" cy="32" r="30" fill="#0B111E" stroke="#FF6B00" stroke-width="2"/>
                            <path d="M22,42h5v12h-5z M30,34h5v20h-5z M38,26h5v28h-5z" fill="#00E676"/>
                            <path d="M18,48 L32,36 L40,40 L50,22" stroke="#00E676" stroke-width="2.5" stroke-linecap="round"/>
                        </svg>
                    </div>
                    <div>
                        <div class="drawer-title">IntraBot <span class="drawer-pro-badge">PRO</span></div>
                        <div class="drawer-sub">QuantEdge Automated Trading</div>
                    </div>
                </div>
                <button class="drawer-close-btn" onclick="window.toggleHamburgerDrawer()" aria-label="Close Menu">✕</button>
            </div>

            <div class="drawer-status-card">
                <div class="status-pulse-dot"></div>
                <div>
                    <div class="status-label">ENGINE STATUS</div>
                    <div class="status-val">Connected • Live Cloud</div>
                </div>
            </div>

            <div class="drawer-nav-list">
                <div class="drawer-section-title">CORE TERMINALS</div>
                <div class="drawer-nav-item ${isHome ? 'active' : ''}" onclick="window.navigateToPage('terminal')">
                    <span class="drawer-nav-icon">📊</span>
                    <span class="drawer-nav-text">Intraday Terminal</span>
                    <span class="drawer-nav-badge">LIVE</span>
                </div>
                <div class="drawer-nav-item ${isDual ? 'active' : ''}" onclick="window.navigateToPage('dual')">
                    <span class="drawer-nav-icon">🔄</span>
                    <span class="drawer-nav-text">Dual Strategy Engine</span>
                    <span class="drawer-nav-badge">ORB+MOM</span>
                </div>
                <div class="drawer-nav-item" onclick="window.navigateToPage('pipeline')">
                    <span class="drawer-nav-icon">⚡</span>
                    <span class="drawer-nav-text">5-Layer AI Pipeline Radar</span>
                </div>

                <div class="drawer-section-title">ANALYTICS &amp; VALIDATION</div>
                <div class="drawer-nav-item" onclick="window.navigateToPage('history')">
                    <span class="drawer-nav-icon">📋</span>
                    <span class="drawer-nav-text">Trade History &amp; Orders</span>
                </div>
                <div class="drawer-nav-item" onclick="window.navigateToPage('backtest')">
                    <span class="drawer-nav-icon">🧪</span>
                    <span class="drawer-nav-text">AI Backtest &amp; Walk-Forward</span>
                </div>
                <div class="drawer-nav-item" onclick="window.navigateToPage('watchlist')">
                    <span class="drawer-nav-icon">👁️</span>
                    <span class="drawer-nav-text">High-Momentum Watchlist</span>
                </div>

                <div class="drawer-section-title">SYSTEM &amp; CREDENTIALS</div>
                <div class="drawer-nav-item ${isCreds ? 'active' : ''}" onclick="window.navigateToPage('credentials')">
                    <span class="drawer-nav-icon">🔑</span>
                    <span class="drawer-nav-text">Upstox Broker Credentials</span>
                </div>
                <div class="drawer-nav-item" onclick="window.navigateToPage('settings')">
                    <span class="drawer-nav-icon">⚙️</span>
                    <span class="drawer-nav-text">Risk Controls &amp; Capital</span>
                </div>
                <div class="drawer-nav-item" onclick="window.navigateToPage('server')">
                    <span class="drawer-nav-icon">🔌</span>
                    <span class="drawer-nav-text">Direct Server Connection</span>
                </div>
                <div class="drawer-nav-item" onclick="window.toggleThemeMode()">
                    <span class="drawer-nav-icon">🌓</span>
                    <span class="drawer-nav-text">Toggle Theme (Dark / Light)</span>
                </div>

                <div class="drawer-section-title">CONTROLS</div>
                <div class="drawer-nav-item text-danger" onclick="window.triggerEmergencyHalt()">
                    <span class="drawer-nav-icon">🚨</span>
                    <span class="drawer-nav-text">Emergency Kill-Switch (HALT)</span>
                </div>
                <div class="drawer-nav-item" onclick="window.navigateToPage('logout')">
                    <span class="drawer-nav-icon">🚪</span>
                    <span class="drawer-nav-text">Logout</span>
                </div>
            </div>

            <div class="drawer-footer">
                <div>IntraBot Standalone v2.4</div>
                <div class="drawer-footer-host" id="drawer-current-host">Connecting...</div>
            </div>
        `;
        document.body.appendChild(drawer);

        // Update host display
        setTimeout(() => {
            const hostEl = document.getElementById("drawer-current-host");
            if (hostEl) {
                const current = (isAndroid && window.AndroidBridge.getServerUrl) 
                    ? window.AndroidBridge.getServerUrl() 
                    : window.location.origin;
                hostEl.innerText = "Host: " + current;
            }
        }, 300);
    }

    // ── Navigation Router ─────────────────────────────────────────────────────
    window.navigateToPage = function (target) {
        haptic("light");
        closeDrawer();

        const isDual = window.location.pathname.endsWith("dual.html") || window.location.pathname.endsWith("/dual");

        if (target === "terminal") {
            if (isDual) {
                window.location.href = (window.location.protocol === "file:") ? "index.html" : "/";
            } else {
                const hist = document.getElementById("history-screen");
                if (hist) hist.style.display = "none";
                window.scrollTo({ top: 0, behavior: "smooth" });
            }
        } else if (target === "dual") {
            if (!isDual) {
                window.location.href = (window.location.protocol === "file:") ? "dual.html" : "/dual";
            }
        } else if (target === "pipeline") {
            if (isDual) {
                window.location.href = (window.location.protocol === "file:") ? "index.html?tab=pipeline" : "/?tab=pipeline";
            } else {
                const radar = document.querySelector(".panel") || document.querySelector(".intelligence-panel");
                if (radar) radar.scrollIntoView({ behavior: "smooth" });
            }
        } else if (target === "history") {
            if (typeof toggleHistoryScreen === "function") {
                toggleHistoryScreen();
            } else {
                const btnHist = document.getElementById("btn-history") || document.querySelector("[onclick*='toggleHistory']");
                if (btnHist) btnHist.click();
            }
        } else if (target === "backtest") {
            const btnBt = document.getElementById("btn-backtest") || document.querySelector("[onclick*='Backtest']");
            if (btnBt) {
                btnBt.click();
            } else {
                showToast("Backtest Engine ready in terminal");
            }
        } else if (target === "watchlist") {
            const wl = document.querySelector(".watchlist-panel") || document.querySelector("[data-panel='watchlist']");
            if (wl) wl.scrollIntoView({ behavior: "smooth" });
        } else if (target === "credentials") {
            window.location.href = (window.location.protocol === "file:") ? "credentials.html" : "/credentials.html";
        } else if (target === "settings") {
            if (typeof openConfig === "function") {
                openConfig();
            } else {
                const btnCfg = document.getElementById("btn-config");
                if (btnCfg) btnCfg.click();
            }
        } else if (target === "server") {
            if (isAndroid && window.AndroidBridge.showServerSettingsDialog) {
                window.AndroidBridge.showServerSettingsDialog();
            } else {
                openServerModal();
            }
        } else if (target === "logout") {
            if (typeof logoutUser === "function") {
                logoutUser();
            } else {
                window.location.href = "/logout";
            }
        }
    };

    window.toggleThemeMode = function () {
        haptic("light");
        const btn = document.getElementById("theme-toggle") || document.querySelector(".theme-btn");
        if (btn) btn.click();
    };

    window.triggerEmergencyHalt = function () {
        haptic("heavy");
        if (confirm("⚠️ Trigger EMERGENCY HALT? This will immediately cancel pending orders and square off open positions.")) {
            const btnHalt = document.getElementById("btn-halt") || document.querySelector(".btn-danger");
            if (btnHalt) btnHalt.click();
        }
    };

    // ── Mobile Header Injection ───────────────────────────────────────────────
    function setupMobileHeader() {
        if (document.getElementById("android-mobile-header")) return;

        const header = document.createElement("header");
        header.id = "android-mobile-header";
        header.innerHTML = `
            <div class="mob-header-left">
                <button class="mob-hamburger-btn" onclick="window.toggleHamburgerDrawer()" aria-label="Open Navigation Menu">
                    <span></span>
                    <span></span>
                    <span></span>
                </button>
                <div class="mob-header-brand" onclick="window.navigateToPage('terminal')">
                    <div class="mob-logo-box">
                        <svg viewBox="0 0 64 64" width="22" height="22" fill="none">
                            <circle cx="32" cy="32" r="30" fill="#0B111E" stroke="#FF6B00" stroke-width="2.5"/>
                            <path d="M22,42h4v10h-4z M30,34h4v18h-4z M38,26h4v26h-4z" fill="#00E676"/>
                            <path d="M18,48 L32,36 L40,40 L50,22" stroke="#00E676" stroke-width="2.8" stroke-linecap="round"/>
                        </svg>
                    </div>
                    <span class="mob-brand-text">IntraBot</span>
                    <span class="mob-pill">PRO</span>
                </div>
            </div>
            <div class="mob-header-right">
                <div class="mob-status-pill" id="mob-engine-status" onclick="window.toggleHamburgerDrawer()">
                    <span class="mob-status-dot"></span>
                    <span class="mob-status-text">LIVE</span>
                </div>
                <button class="mob-halt-btn" onclick="window.triggerEmergencyHalt()" title="Emergency Kill-Switch">
                    🛑 HALT
                </button>
            </div>
        `;

        // Prepend before main app
        document.body.prepend(header);
    }

    // ── Mobile Bottom Navigation Dock ─────────────────────────────────────────
    function setupMobileBottomNav() {
        if (document.getElementById("android-bottom-nav")) return;

        const isDual = window.location.pathname.endsWith("dual.html") || window.location.pathname.endsWith("/dual");

        const nav = document.createElement("nav");
        nav.id = "android-bottom-nav";
        nav.innerHTML = `
            <div class="mob-nav-item ${!isDual ? 'active' : ''}" data-tab="terminal" onclick="window.navigateToPage('terminal')">
                <span class="mob-nav-icon">📊</span>
                <span class="mob-nav-label">Terminal</span>
            </div>
            <div class="mob-nav-item ${isDual ? 'active' : ''}" data-tab="dual" onclick="window.navigateToPage('dual')">
                <span class="mob-nav-icon">🔄</span>
                <span class="mob-nav-label">Dual Mode</span>
            </div>
            <div class="mob-nav-item" data-tab="pipeline" onclick="window.navigateToPage('pipeline')">
                <span class="mob-nav-icon">⚡</span>
                <span class="mob-nav-label">Radar</span>
            </div>
            <div class="mob-nav-item" data-tab="history" onclick="window.navigateToPage('history')">
                <span class="mob-nav-icon">📋</span>
                <span class="mob-nav-label">Trades</span>
            </div>
            <div class="mob-nav-item" data-tab="menu" onclick="window.toggleHamburgerDrawer()">
                <span class="mob-nav-icon">☰</span>
                <span class="mob-nav-label">Menu</span>
            </div>
        `;
        document.body.appendChild(nav);
    }

    // ── Direct Server Connection Modal (Fallback for Browser/Web) ─────────────
    function openServerModal() {
        let modal = document.getElementById("mobile-server-modal");
        if (!modal) {
            modal = document.createElement("div");
            modal.id = "mobile-server-modal";
            modal.innerHTML = `
                <div class="msm-content">
                    <div class="msm-header">
                        <h3>🔌 Direct Server Connection</h3>
                        <button onclick="document.getElementById('mobile-server-modal').style.display='none'">✕</button>
                    </div>
                    <p style="font-size:12px;color:#94a3b8;margin-bottom:12px;">Connect your mobile app directly to your cloud trading host URL.</p>
                    <input type="text" id="msm-url-input" class="msm-input" placeholder="https://bot.deepcodev.site" />
                    <div class="msm-presets">
                        <button onclick="document.getElementById('msm-url-input').value='https://bot.deepcodev.site'">Cloud Live</button>
                        <button onclick="document.getElementById('msm-url-input').value='http://10.0.2.2:8000'">Emulator</button>
                    </div>
                    <div class="msm-actions">
                        <button class="msm-btn-save" onclick="saveDirectServerUrl()">Save &amp; Connect</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
        }

        const input = document.getElementById("msm-url-input");
        if (input) {
            input.value = (isAndroid && window.AndroidBridge.getServerUrl) 
                ? window.AndroidBridge.getServerUrl() 
                : (localStorage.getItem("_ib_server_url") || window.location.origin);
        }
        modal.style.display = "flex";
    }

    window.saveDirectServerUrl = function () {
        const input = document.getElementById("msm-url-input");
        if (!input) return;
        const url = input.value.trim();
        if (!url) return;

        if (isAndroid && window.AndroidBridge.saveServerUrl) {
            window.AndroidBridge.saveServerUrl(url);
            window.AndroidBridge.loadTradingTerminal();
        } else {
            localStorage.setItem("_ib_server_url", url);
            window.location.href = url;
        }
    };

    // ── Initialization Loop ───────────────────────────────────────────────────
    function init() {
        setupMobileHeader();
        setupHamburgerDrawer();
        setupMobileBottomNav();

        // Safe area adjustments
        document.documentElement.style.setProperty("--app-height", `${window.innerHeight}px`);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // Secondary backup run after page resources stabilize
    window.addEventListener("load", init);

})();
