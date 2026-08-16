// ============================================================
// SCRIPT.JS — Daksh Grand Earn (Clean Rewrite)
// ============================================================

// Telegram WebApp is not available when the page is opened directly in a
// browser. The old code accessed window.Telegram.WebApp immediately, so one
// missing global stopped the entire script and made every button appear dead.
const tg = window.Telegram?.WebApp ?? {
    initDataUnsafe: {},
    ready() {},
    expand() {},
    enableClosingConfirmation() {},
    openTelegramLink(url) { window.open(url, '_blank', 'noopener,noreferrer'); },
    openLink(url) { window.open(url, '_blank', 'noopener,noreferrer'); },
};

tg.ready();
tg.expand();
tg.enableClosingConfirmation();

// config.js is optional during local/browser previews. Keep all properties
// present so a missing config shows a useful toast instead of a ReferenceError.
// If config.js declares `const CONFIG`, merge defaults into that same object
// instead of creating a second disconnected configuration object.
const DAKSH_DEFAULT_CONFIG = {
    API_BASE_URL: '',
    BOT_USERNAME: '',
    ADMIN_TELEGRAM: '',
    ADMIN_UPI: '',
    ADMIN_QR_URL: '',
    MONETAG_ZONE_ID: '',
    MONETAG_SDK_URL: '',
    CLAIM_AD_ENABLED: true,
    REFERRAL_ACTIVE: true,
    LOTTERY_ACTIVE: false,
    YT_TASKS_ACTIVE: true,
    WEB_LINKS: {},
    YT_LINKS: {},
    PARTNER_LINKS: {},
    CHANNELS: {
        official: '#',
        channel2: '#',
        channel3: '#',
    },
    SPONSORS: {},
};

if (typeof CONFIG === 'undefined') {
    window.CONFIG = DAKSH_DEFAULT_CONFIG;
} else {
    Object.keys(DAKSH_DEFAULT_CONFIG).forEach(key => {
        if (CONFIG[key] === undefined) CONFIG[key] = DAKSH_DEFAULT_CONFIG[key];
    });
    window.CONFIG = CONFIG;
}

const userId = tg.initDataUnsafe?.user?.id;

window.USER_ID = userId;
// Store full user object for tournament + other features
window._tgUser = tg.initDataUnsafe?.user || null;

let userData = {};
let _winnerPopupShown    = false;   // guard: show winner popup only once per session
const _pendingRequests   = new Set();
let monetagSdkPromise    = null;
let monetagPreloaded     = false;
// BUG FIX #1: Block confirmation guard — need 2 consecutive 'blocked' responses
// to avoid cold-start / temporary API error triggering showBlockedView()
let _blockVotes          = 0;
// BUG FIX #2: fetchLiveData overlap guard — prevent concurrent API calls
let _fetchLiveDataRunning = false;
// Tournament T&C — accepted once per session
let _tournamentTncAccepted = false;

// ============================================================
// CONSTANTS
// ============================================================
const MAX_ADS_PER_DAY       = 10;
const MAX_YT_PER_DAY        = 3;
const MAX_WEB_PER_DAY       = 3;
const MIN_WITHDRAW_COINS    = 25000;
const ALL_TASKS_BONUS       = 10;
const BOMB_BOX_COOLDOWN_SECS = 900;

// ============================================================
// MONETAG SDK
// ============================================================
function getMonetagZoneId() {
    return String(CONFIG.MONETAG_ZONE_ID || "").trim();
}

function getMonetagShowFunction() {
    const zoneId = getMonetagZoneId();
    if (!zoneId) return null;
    return window[`show_${zoneId}`];
}

function loadMonetagSdk() {
    const zoneId = getMonetagZoneId();
    const sdkUrl = String(CONFIG.MONETAG_SDK_URL || "").trim();
    if (!zoneId) return Promise.reject(new Error("Monetag Zone ID missing"));
    if (getMonetagShowFunction()) return Promise.resolve();
    if (!sdkUrl) return Promise.reject(new Error("Monetag SDK URL missing"));
    if (monetagSdkPromise) return monetagSdkPromise;

    monetagSdkPromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.async = true;
        script.src = sdkUrl;
        script.dataset.zone = zoneId;
        script.dataset.sdk  = `show_${zoneId}`;
        script.onload  = () => getMonetagShowFunction()
            ? resolve()
            : reject(new Error("Monetag show function not found"));
        script.onerror = () => reject(new Error("Monetag SDK failed to load"));
        document.head.appendChild(script);
    }).catch(err => { monetagSdkPromise = null; throw err; });

    return monetagSdkPromise;
}

async function preloadMonetagAd() {
    if (!userId || !getMonetagZoneId()) return;
    try {
        await loadMonetagSdk();
        const showAd = getMonetagShowFunction();
        if (!showAd) return;
        await showAd({ type: 'preload', timeout: 5, ymid: String(userId), requestVar: 'ad_reward' });
        monetagPreloaded = true;
    } catch (e) {
        monetagPreloaded = false;
    }
}

// ============================================================
// TOAST
// ============================================================
function showToast(msg, type = "info") {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        document.body.appendChild(toast);
    }
    // Some older handlers use "ok"; keep the visual state consistent.
    if (type === 'ok') type = 'success';
    toast.textContent = String(msg || '')
        .replace(/\p{Extended_Pictographic}(?:\uFE0F|\u200D\p{Extended_Pictographic})*/gu, '')
        .replace(/\s{2,}/g, ' ')
        .trim();
    toast.className = `show ${type}`;
    setTimeout(() => { toast.className = ''; }, 3500);
}

function openExternalLink(url) {
    const target = String(url || '').trim();
    if (!target || target === '#') return false;

    try {
        // Telegram's WebApp APIs are more reliable than window.open inside
        // the Mini App iframe and preserve the user's expected navigation.
        if (target.startsWith('https://t.me/') && tg?.openTelegramLink) {
            tg.openTelegramLink(target);
        } else if (tg?.openLink) {
            tg.openLink(target);
        } else {
            window.open(target, '_blank', 'noopener,noreferrer');
        }
        return true;
    } catch (error) {
        // Browser previews can still open regular links even if Telegram's
        // bridge rejects the call.
        try {
            window.open(target, '_blank', 'noopener,noreferrer');
            return true;
        } catch (_) {
            showToast('Unable to open this link. Please try again.', 'error');
            return false;
        }
    }
}

// ============================================================
// FETCH WITH RETRY — 3 retries, 10s timeout
// ============================================================
async function fetchWithRetry(url, options = {}, retries = 3, delayMs = 2000) {
    for (let attempt = 1; attempt <= retries; attempt++) {
        let timeout;
        try {
            const controller = new AbortController();
            timeout = setTimeout(() => controller.abort(), 10000);
            const res        = await fetch(url, { ...options, signal: controller.signal });
            if (!res.ok && res.status >= 400 && res.status < 500) return res;
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return res;
        } catch (err) {
            if (attempt === retries) throw err;
            await new Promise(r => setTimeout(r, delayMs));
        } finally {
            if (timeout) clearTimeout(timeout);
        }
    }
}

// ============================================================
// SHARED /get_feature_config FETCH — dedupe + short cache
// ============================================================
// BUG FIX: spin/mining/premium/web-tasks/bomb-box each fetched this same
// endpoint independently on every refresh — 5 separate network calls for
// data that's identical across all of them. Beyond the waste, this made it
// far too easy to trip the backend's per-IP rate-limit/ban from completely
// normal usage (a handful of real users refreshing around the same time).
// Now: one in-flight request is shared by all callers, and the result is
// cached for a few seconds so a full refresh cycle costs 1 call, not 5.
let _featureCfgCache        = null;
let _featureCfgCacheTime    = 0;
let _featureCfgInFlight     = null;
const FEATURE_CFG_CACHE_MS  = 4000;

async function getFeatureConfig() {
    const now = Date.now();
    if (_featureCfgCache && (now - _featureCfgCacheTime) < FEATURE_CFG_CACHE_MS) {
        return _featureCfgCache;
    }
    if (_featureCfgInFlight) return _featureCfgInFlight;

    _featureCfgInFlight = (async () => {
        try {
            const res = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_feature_config`);
            const cfg = await res.json();
            _featureCfgCache     = cfg;
            _featureCfgCacheTime = Date.now();
            return cfg;
        } finally {
            _featureCfgInFlight = null;
        }
    })();
    return _featureCfgInFlight;
}

// ============================================================
// COUNTDOWN HELPER
// ============================================================
function startCountdown(seconds, updateFn, doneFn) {
    let remaining = seconds;
    updateFn(remaining);
    const interval = setInterval(() => {
        remaining--;
        if (remaining <= 0) {
            clearInterval(interval);
            doneFn();
        } else {
            updateFn(remaining);
        }
    }, 1000);
    return interval;
}

// ============================================================
// STREAK BONUS SYSTEM
// Days 1-10: 10 coins | Days 11-20: 15 coins | Days 21-30: 20 coins
// >48h gap → streak reset to Day 1
// ============================================================
let _dailyCountdownInterval = null;

function parseUTCTimestamp(ts) {
    if (!ts) return null;
    try {
        const str = ts.includes('Z') || ts.includes('+') ? ts : ts + 'Z';
        const d = new Date(str);
        return isNaN(d.getTime()) ? null : d;
    } catch (e) { return null; }
}

function _getStreakReward(day) {
    if (day <= 10) return 10;
    if (day <= 20) return 15;
    return 20;
}

function _getStreakTier(day) {
    if (day <= 10) return { label: 'Tier 1', color: '#f1c40f', emoji: '🔥' };
    if (day <= 20) return { label: 'Tier 2', color: '#38bdf8', emoji: '⚡' };
    return { label: 'Tier 3', color: '#a855f7', emoji: '💎' };
}

function updateStreakUI(streakDay) {
    streakDay = Math.max(0, parseInt(streakDay) || 0);
    const displayDay  = streakDay === 0 ? 1 : streakDay;
    const nextDay     = (streakDay % 30) + 1;
    const reward      = _getStreakReward(displayDay);
    const nextReward  = _getStreakReward(nextDay);
    const tier        = _getStreakTier(displayDay);

    const dayEl    = document.getElementById('streak-day-num');
    const rewardEl = document.getElementById('streak-reward-num');
    const tierEl   = document.getElementById('streak-tier-label');
    const barEl    = document.getElementById('streak-progress-bar');
    const nextEl   = document.getElementById('streak-next-reward');

    if (dayEl)    dayEl.textContent    = displayDay;
    if (rewardEl) rewardEl.textContent = `+${reward} 🪙`;
    if (tierEl) {
        tierEl.textContent  = `${tier.emoji} ${tier.label}`;
        tierEl.style.color  = tier.color;
    }
    if (nextEl) {
        if (streakDay >= 30) {
            nextEl.textContent = '🏆 Max streak reached! Restarting Day 1 next.';
        } else {
            nextEl.textContent = `Day ${nextDay}: +${nextReward} 🪙`;
        }
    }
    // Progress bar within current tier (10-day blocks)
    if (barEl) {
        const block = streakDay <= 10 ? streakDay : streakDay <= 20 ? streakDay - 10 : streakDay - 20;
        const pct   = (block / 10) * 100;
        barEl.style.width      = pct + '%';
        barEl.style.background = tier.color;
    }
}

function startDailyCountdown(remainingSeconds) {
    if (_dailyCountdownInterval) clearInterval(_dailyCountdownInterval);

    const btn     = document.getElementById('daily-btn');
    const timerEl = document.getElementById('daily-timer');
    const countEl = document.getElementById('daily-countdown');

    let secs = Math.max(0, Math.floor(remainingSeconds));

    if (btn) { btn.disabled = true; btn.style.opacity = '0.6'; btn.innerText = 'Come Back Later'; }
    if (timerEl) timerEl.style.display = 'block';

    const fmt = n => String(n).padStart(2, '0');

    const tick = () => {
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = secs % 60;
        const display = `${fmt(h)}:${fmt(m)}:${fmt(s)}`;
        if (countEl) countEl.textContent = display;
        if (btn) btn.innerText = `⏰ ${display} left`;
    };

    tick();

    _dailyCountdownInterval = setInterval(() => {
        secs--;
        if (secs <= 0) {
            clearInterval(_dailyCountdownInterval);
            _dailyCountdownInterval = null;
            if (timerEl) timerEl.style.display = 'none';
            if (btn) { btn.disabled = false; btn.style.opacity = '1'; btn.innerText = '🔥 Claim Streak'; }
            showToast('🔥 Streak bonus ready! Claim now!', 'success');
            return;
        }
        tick();
    }, 1000);
}

function checkDailyBonus(lastClaimTs) {
    const btn = document.getElementById('daily-btn');
    if (!btn) return;

    if (!lastClaimTs) {
        btn.disabled = false; btn.style.opacity = '1'; btn.innerText = '🔥 Claim Streak';
        const timerEl = document.getElementById('daily-timer');
        if (timerEl) timerEl.style.display = 'none';
        if (_dailyCountdownInterval) clearInterval(_dailyCountdownInterval);
        return;
    }

    const lastDt = parseUTCTimestamp(lastClaimTs);
    if (!lastDt) { btn.disabled = false; btn.innerText = '🔥 Claim Streak'; return; }

    const diffSec  = (Date.now() - lastDt.getTime()) / 1000;
    const totalSec = 24 * 3600;

    if (diffSec < totalSec) {
        const remaining = Math.ceil(totalSec - diffSec);
        if (!_dailyCountdownInterval) startDailyCountdown(remaining);
    } else {
        if (_dailyCountdownInterval) { clearInterval(_dailyCountdownInterval); _dailyCountdownInterval = null; }
        btn.disabled = false; btn.style.opacity = '1'; btn.innerText = '🔥 Claim Streak';
        const timerEl = document.getElementById('daily-timer');
        if (timerEl) timerEl.style.display = 'none';
    }
}

async function claimDaily() {
    if (!userId) return showToast('User ID not found!', 'error');
    if (_pendingRequests.has('claimDaily')) return;
    _pendingRequests.add('claimDaily');

    const btn = document.getElementById('daily-btn');
    if (btn) { btn.disabled = true; btn.innerText = '📺 Watch Ad...'; }

    let claimToken = null;
    if (CONFIG.CLAIM_AD_ENABLED) {
        try {
            const tokenRes  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/daily_claim_token/${userId}`, { method: 'POST' });
            const tokenData = await tokenRes.json();
            if (tokenData.status !== 'success' || !tokenData.token) {
                showToast(tokenData.message || 'Could not start ad. Try again.', 'error');
                const remSecs = tokenData.data?.remaining_seconds;
                if (remSecs && remSecs > 0) startDailyCountdown(remSecs);
                else if (btn) { btn.disabled = false; btn.innerText = '🔥 Claim Streak'; }
                _pendingRequests.delete('claimDaily');
                return;
            }
            claimToken = tokenData.token;
        } catch (e) {
            showToast('⚠️ Server error. Please retry.', 'error');
            if (btn) { btn.disabled = false; btn.innerText = '🔥 Claim Streak'; }
            _pendingRequests.delete('claimDaily');
            return;
        }

        try {
            await requireAdWatch();
        } catch (e) {
            showToast('📺 Watch the full ad to claim your streak bonus!', 'error');
            if (btn) { btn.disabled = false; btn.innerText = '🔥 Claim Streak'; }
            _pendingRequests.delete('claimDaily');
            return;
        }
    }

    if (btn) btn.innerText = 'Claiming...';
    try {
        const body = claimToken ? JSON.stringify({ token: claimToken }) : undefined;
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/claim_daily/${userId}`, {
            method:  'POST',
            headers: claimToken ? { 'Content-Type': 'application/json' } : {},
            body,
        });
        const data = await res.json();

        if (data.status === 'success') {
            const day    = data.data?.streak_day || 1;
            const reward = data.data?.bonus || 10;
            showToast(`🔥 Day ${day} Streak! +${reward} coins added!`, 'success');
            updateStreakUI(day);
            startDailyCountdown(24 * 3600);
            fetchLiveData();
        } else {
            showToast(data.message || 'Already claimed today.', 'error');
            const remSecs = data.data?.remaining_seconds;
            if (remSecs && remSecs > 0) startDailyCountdown(remSecs);
            else if (btn) { btn.disabled = false; btn.innerText = '🔥 Claim Streak'; }
        }
    } catch (e) {
        showToast('⚠️ Error! Please retry.', 'error');
        if (btn) { btn.disabled = false; btn.innerText = '🔥 Claim Streak'; }
    } finally {
        _pendingRequests.delete('claimDaily');
    }
}

// ============================================================
// MAIN DATA FETCH
// ============================================================
async function fetchLiveData() {
    // BUG FIX #2: Prevent overlapping concurrent calls
    if (_fetchLiveDataRunning) return;
    _fetchLiveDataRunning = true;

    if (!userId) {
        const bal = document.getElementById('balance');
        if (bal) bal.innerText = "ID Error";
        _fetchLiveDataRunning = false;
        return;
    }
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_user/${userId}`);
        const data = await res.json();

        // BUG FIX #1: Require 2 consecutive 'blocked' responses before blocking UI.
        // This prevents a Render cold-start or temp error from hiding all tabs.
        if (data.status === "blocked") {
            _blockVotes++;
            if (_blockVotes >= 2) {
                // BUG FIX: backend now still sends profile fields (coins, referrals,
                // rank, etc.) alongside status:"blocked" — populate the Profile tab
                // with them before switching to the blocked view, so a banned user
                // can still see their own profile, not just Support.
                userData = data;
                loadProfileTab();
                showBlockedView();
                return;
            }
            // First vote — retry once after a short delay to confirm
            setTimeout(fetchLiveData, 5000);
            _fetchLiveDataRunning = false;
            return;
        }
        // Reset block votes on any successful non-blocked response
        _blockVotes = 0;

        if (data.status === "success") {
            userData = data;

            const coins      = data.coins || 0;
            const refCount   = getRefCount(data.referrals);
            const premInfo   = data.premium_info || {};
            const isPremium  = !!premInfo.premium;

            // Dynamic withdrawal threshold based on premium
            const _minWd    = isPremium ? 10000 : MIN_WITHDRAW_COINS;
            const _refNeeded = isPremium ? 2 : 5;

            const balEl = document.getElementById('balance');
            if (balEl) balEl.innerText = `${coins} 🪙`;

            // Rupee balance display
            const rupees = parseFloat(data.rupees || 0).toFixed(2);
            const rupeeBalEl = document.getElementById('rupee-balance');
            const rupeeWdEl  = document.getElementById('rupee-wd-balance-text');
            if (rupeeBalEl) rupeeBalEl.textContent = rupees;
            if (rupeeWdEl)  rupeeWdEl.textContent  = '₹' + rupees;

            // ── Premium badge in balance area ─────────────────────────────
            const premBadgeEl = document.getElementById('premium-status-badge');
            if (premBadgeEl) {
                if (isPremium) {
                    premBadgeEl.innerHTML = `
                        <span style="
                            display:inline-flex;align-items:center;gap:5px;
                            background:linear-gradient(135deg,#1a78c2,#1D9BF0);
                            color:#fff;font-size:11px;font-weight:800;
                            padding:4px 11px 4px 8px;border-radius:20px;
                            letter-spacing:0.4px;box-shadow:0 2px 8px rgba(29,155,240,0.4);
                        ">
                            <svg width="14" height="14" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
                                <circle cx="9" cy="9" r="9" fill="rgba(255,255,255,0.25)"/>
                                <path d="M5 9.5L7.5 12L13 6.5" stroke="white" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                            VERIFIED PREMIUM · ${premInfo.days_left}d left
                        </span>`;
                    premBadgeEl.style.display = 'block';
                } else {
                    premBadgeEl.style.display = 'none';
                }
            }
            // ─────────────────────────────────────────────────────────────

            const coinsPct = Math.min((coins / _minWd) * 100, 100);
            const refPct   = Math.min((refCount / _refNeeded) * 100, 100);

            const coinsBar  = document.getElementById('coins-progress-bar');
            const refBar    = document.getElementById('ref-progress-bar');
            const coinsText = document.getElementById('coins-progress-text');
            const refText   = document.getElementById('ref-progress-text');

            if (coinsBar) {
                coinsBar.style.width      = coinsPct + '%';
                coinsBar.style.background = coins >= _minWd
                    ? 'linear-gradient(90deg,#2ecc71,#27ae60)'
                    : 'linear-gradient(90deg,#f1c40f,#f39c12)';
            }
            if (refBar)    refBar.style.width    = refPct + '%';
            if (coinsText) coinsText.innerText   = `${coins} / ${_minWd}${coins >= _minWd ? ' ✅' : ''}${isPremium ? ' ✓' : ''}`;
            if (refText)   refText.innerText     = `${refCount} / ${_refNeeded}${refCount >= _refNeeded ? ' ✅' : ''}${isPremium ? ' ✓' : ''}`;

            applyReferralLock();
            // Update rupee withdrawal referral progress UI
            updateRupeeRefUI(refCount, isPremium ? 2 : 5);

            // BUG FIX #4: Only update leaderboard UI when leaderboard tab is actually visible
            const _lbTabActive = document.getElementById('leaderboard')?.classList.contains('active-tab');
            if (_lbTabActive && data.leaderboard && data.leaderboard !== "none") updateLeaderboardUI(data.leaderboard);

            const linkEl = document.getElementById('display-link');
            if (linkEl) linkEl.innerText = `https://t.me/${CONFIG.BOT_USERNAME}?start=${userId}`;

            updateReferralList(data.referrals);
            applyCompletedTasks(data.completed_tasks || []);
            checkDailyBonus(data.last_claim);
            updateStreakUI(data.streak_day || 0);
            updateAdCounter(data.ads_today || 0, data.ads_date || "");
            updateChannelButtons(data.channel_claims || {});
            renderSponsorSlots(data.channel_claims || {}, data.completed_tasks || [], data.verify_completions || {});

            window._promoTaskCompletions = data.promo_task_completions || [];
            updateAllBonusUI(data);
            if (typeof loadPromoTasks === 'function') loadPromoTasks();
            loadLotteryStatus();
            loadSpinStatus();
            loadMiningStatus();
            loadBombBoxStatus();
            loadWebTasksStatus();
            loadPremiumCardStatus();
            loadVipTasks();
            loadProfileTab();

            // Update withdraw minimum check with premium dynamic value
            window._dynamicMinWithdraw = isPremium ? 10000 : MIN_WITHDRAW_COINS;

            // Update premium card on home tab
            updatePremiumCard(premInfo);

            if (data.pending_winner_popup && !_winnerPopupShown) showWinnerPopup(data.pending_winner_prize || 0);
        }
    } catch (err) {
        showToast("⚠️ Connection error. Retrying...", "error");
        setTimeout(fetchLiveData, 15000);
    } finally {
        // BUG FIX #2: Always release the running lock
        _fetchLiveDataRunning = false;
    }
}

// ============================================================
// LOTTERY WINNER POPUP
// ============================================================
function _spawnConfetti() {
    const colors = ['#f1c40f','#e74c3c','#2ecc71','#3b82f6','#a855f7','#f97316','#ec4899','#fff'];
    for (let i = 0; i < 60; i++) {
        const el = document.createElement('div');
        el.className = 'confetti-piece';
        el.style.cssText = [
            `left: ${Math.random() * 100}vw`,
            `background: ${colors[Math.floor(Math.random() * colors.length)]}`,
            `width: ${6 + Math.random() * 10}px`,
            `height: ${6 + Math.random() * 10}px`,
            `border-radius: ${Math.random() > 0.5 ? '50%' : '2px'}`,
            `animation-duration: ${2.5 + Math.random() * 2.5}s`,
            `animation-delay: ${Math.random() * 1.2}s`,
        ].join(';');
        document.body.appendChild(el);
        setTimeout(() => el.remove(), 6000);
    }
}

function showWinnerPopup(prize) {
    const overlay = document.getElementById('winner-popup-overlay');
    const prizeEl = document.getElementById('winner-prize-coins');
    if (!overlay) return;
    if (_winnerPopupShown) return;   // double-show guard
    _winnerPopupShown = true;        // immediately lock — prevent fetchLiveData from triggering again
    if (prizeEl) prizeEl.innerText = `+${prize} 🪙`;
    overlay.style.display = 'flex';
    _spawnConfetti();
    setTimeout(_spawnConfetti, 900);
    if (userId) {
        fetchWithRetry(`${CONFIG.API_BASE_URL}/ack_winner_popup/${userId}`, { method: 'POST' }).catch(() => {});
    }
}

function closeWinnerPopup() {
    const overlay = document.getElementById('winner-popup-overlay');
    if (overlay) {
        overlay.style.opacity = '0';
        overlay.style.transition = 'opacity 0.35s';
        setTimeout(() => { overlay.style.display = 'none'; overlay.style.opacity = ''; }, 360);
    }
}

function getRefCount(referrals) {
    if (!referrals || referrals === "" || referrals === "none") return 0;
    return referrals.split(',').filter(id => id.trim() !== '').length;
}

// ============================================================
// LOTTERY
// ============================================================
async function loadLotteryStatus() {
    if (!userId) return;
    const card = document.getElementById('lottery-card');
    if (!card) return;

    if (CONFIG.LOTTERY_ACTIVE === false) {
        card.style.display = 'block';
        // BUG FIX #3/#1: dim via plain class (not just :has()) and always
        // disable the ticket button so a locked lottery can't be bought.
        card.classList.add('locked-card');
        const lockedBtn = document.getElementById('lottery-btn');
        if (lockedBtn) lockedBtn.disabled = true;
        if (!card.querySelector('.lottery-lock-overlay')) {
            const ov = document.createElement('div');
            ov.className = 'lottery-lock-overlay app-lock-pill';
            ov.innerHTML = '<span class="lottery-lock-label">Coming Soon</span>';
            card.prepend(ov);
        }
        return;
    }

    card.classList.remove('locked-card');
    const staleOv = card.querySelector('.lottery-lock-overlay');
    if (staleOv) staleOv.remove();

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_lottery_status?user_id=${userId}`);
        const data = await res.json();

        if (data.status !== 'success' || !data.active) { card.style.display = 'none'; return; }

        card.style.display = 'block';

        const priceEl   = document.getElementById('lottery-ticket-price');
        const prizeEl   = document.getElementById('lottery-prize');
        const playersEl = document.getElementById('lottery-players');
        const btn       = document.getElementById('lottery-btn');
        const winnerEl  = document.getElementById('lottery-last-winner');

        if (priceEl)   priceEl.innerText   = data.ticket_price ?? '--';
        if (prizeEl)   prizeEl.innerText   = data.prize ?? '--';
        if (playersEl) playersEl.innerText = data.tickets_sold ?? 0;

        if (winnerEl) {
            if (data.last_winner?.user_id) {
                const wid = String(data.last_winner.user_id);
                const masked = wid.length > 4 ? `***${wid.slice(-4)}` : wid;
                winnerEl.innerText    = `🏆 Last winner: ${masked} won ${data.last_winner.prize} 🪙`;
                winnerEl.style.display = 'block';
            } else {
                winnerEl.style.display = 'none';
            }
        }

        if (btn) {
            if (data.drawn) {
                btn.disabled = true;
                btn.innerText = "🎲 Today's round drawn — back at 00:00 UTC";
                btn.style.background = '#7f8c8d'; btn.style.color = '#fff';
            } else if (data.has_ticket) {
                btn.disabled = true;
                btn.innerText = '✅ You\'re in! Good luck 🍀';
                btn.style.background = '#27ae60'; btn.style.color = '#fff';
            } else {
                btn.disabled = false;
                btn.innerText = `🎫 Buy Ticket (${data.ticket_price} 🪙)`;
                btn.style.background = '#ffd700'; btn.style.color = '#1a1a1a';
            }
        }
    } catch (err) {
        card.style.display = 'none';
    }
}

async function buyLotteryTicket() {
    if (!userId) return showToast('⚠️ User ID error.', 'error');
    // BUG FIX #1: defense-in-depth — never allow purchase while the feature is locked,
    // even if something re-enabled the button by mistake.
    if (CONFIG.LOTTERY_ACTIVE === false) return showToast('🎫 Lottery coming soon!', 'error');
    const btn = document.getElementById('lottery-btn');
    if (btn?.disabled) return;
    if (btn) { btn.disabled = true; btn.innerText = '📺 Loading Ad...'; }

    // Step 1: Watch ad before ticket purchase
    try {
        if (btn) btn.innerText = '📺 Watching Ad...';
        await requireAdWatch();
    } catch (e) {
        showToast('📺 Watch the full ad to buy a ticket!', 'error');
        if (btn) { btn.disabled = false; btn.innerText = '🎫 Buy Ticket'; }
        return;
    }

    // Step 2: 10-second cooldown after ad
    await _adCooldown(btn, '🎫 Buy Ticket');
    if (btn) { btn.disabled = true; btn.innerText = '⏳ Buying...'; }

    // Step 3: purchase ticket
    try {
        const res  = await fetch(`${CONFIG.API_BASE_URL}/buy_lottery_ticket`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId }),
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast(data.message || '🎫 Ticket purchased!', 'success');
            if (typeof refreshBalance === 'function') refreshBalance();
        } else {
            showToast(data.message || 'Could not buy ticket.', 'error');
        }
    } catch (err) {
        showToast('⚠️ Network error. Try again.', 'error');
    } finally {
        if (btn) btn.disabled = false;
        loadLotteryStatus();
    }
}

// ============================================================
// FEATURE LOCK HELPERS — spin-lock-overlay / mining-lock-overlay
// ============================================================
function _applyFeatureLock(card, overlayClass, label) {
    if (!card) return;
    // BUG FIX #3: force-dim the card via a plain class instead of relying only
    // on the CSS :has() selector (unsupported in some Telegram in-app WebViews),
    // and disable every actionable button inside so the lock can't be bypassed
    // by tapping the button that sits underneath the pill.
    card.classList.add('locked-card');
    card.querySelectorAll('button').forEach(b => { b.disabled = true; });

    if (card.querySelector('.' + overlayClass)) return;
    card.style.overflow = 'hidden';
    const ov = document.createElement('div');
    // BUG FIX #4: was position:absolute pinned to a corner, which kept colliding
    // with whatever badge/button already lived in that corner (rate badge, CTA
    // button, etc — a different collision on every card). Inserted in normal
    // flow as the card's first line instead, so it can never overlap anything.
    ov.className = overlayClass + ' app-lock-pill';
    ov.innerHTML = '<span class="lock-label">' +
        String(label || 'Coming Soon').replace(/Coming Soon!?/i, 'Coming Soon') +
        '</span>';
    card.prepend(ov);
}

// Shared by both lock systems (feature-lock + tournament-lock) since the
// same card can be locked by either one independently. Re-derives the
// dimmed/disabled visual state from whatever lock pills are actually still
// present, instead of one system blindly clearing what the other just set.
function _syncCardLockVisual(card) {
    if (!card) return;
    if (card.querySelector('.app-lock-pill')) {
        card.classList.add('locked-card');
        card.querySelectorAll('button').forEach(b => { b.disabled = true; });
    } else {
        card.classList.remove('locked-card');
        // Safe to blanket re-enable: every card that reaches here (spin/mining/
        // bomb-box/lottery/tabs) immediately re-renders its own correct
        // per-button state right after this runs.
        card.querySelectorAll('button').forEach(b => { b.disabled = false; });
    }
}

function _removeFeatureLock(card, overlayClass) {
    if (!card) return;
    const ov = card.querySelector('.' + overlayClass);
    if (ov) ov.remove();
    _syncCardLockVisual(card);
}

// ============================================================
// 🎡 SPIN WHEEL — Canvas + Sound Engine
// ============================================================

// Segments must match SPIN_REWARDS in main.py: [0, 5, 10, 15, 20, 30, 50, 100]
const WHEEL_SEGMENTS = [
    { label: 'Miss',   coins: 0,   color: '#1e293b', altColor: '#334155', textColor: '#94a3b8' },
    { label: '5',      coins: 5,   color: '#5b21b6', altColor: '#7c3aed', textColor: '#fff'    },
    { label: '10',     coins: 10,  color: '#1e40af', altColor: '#2563eb', textColor: '#fff'    },
    { label: '15',     coins: 15,  color: '#0e7490', altColor: '#0891b2', textColor: '#fff'    },
    { label: '20',     coins: 20,  color: '#065f46', altColor: '#059669', textColor: '#fff'    },
    { label: '30',     coins: 30,  color: '#92400e', altColor: '#d97706', textColor: '#fff'    },
    { label: '50',     coins: 50,  color: '#991b1b', altColor: '#dc2626', textColor: '#fff'    },
    { label: '100',    coins: 100, color: '#854d0e', altColor: '#ca8a04', textColor: '#fef08a' },
];

const _WS_COUNT = WHEEL_SEGMENTS.length;
const _WS_ANGLE = (2 * Math.PI) / _WS_COUNT;
let   _wheelRot = 0;
let   _wheelAnimId = null;
let   _audioCtx    = null;

function _getAudioCtx() {
    if (!_audioCtx || _audioCtx.state === 'closed') {
        try { _audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch(e) {}
    }
    return _audioCtx;
}

function _playTick() {
    try {
        const ctx  = _getAudioCtx(); if (!ctx) return;
        const osc  = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.type = 'square';
        osc.frequency.value = 600 + Math.random() * 300;
        gain.gain.setValueAtTime(0.08, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.04);
        osc.start(); osc.stop(ctx.currentTime + 0.04);
    } catch(e) {}
}

function _playWinSound(coins) {
    try {
        const ctx = _getAudioCtx(); if (!ctx) return;
        if (coins === 0) {
            const osc  = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.connect(gain); gain.connect(ctx.destination);
            osc.type = 'sawtooth';
            osc.frequency.setValueAtTime(280, ctx.currentTime);
            osc.frequency.linearRampToValueAtTime(120, ctx.currentTime + 0.35);
            gain.gain.setValueAtTime(0.18, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
            osc.start(); osc.stop(ctx.currentTime + 0.35);
        } else {
            const notes = coins >= 100 ? [523, 659, 784, 1047, 1319]
                        : coins >= 50  ? [523, 659, 784, 1047]
                        : coins >= 20  ? [523, 659, 784]
                        :                [523, 659];
            notes.forEach((freq, i) => {
                const osc  = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.connect(gain); gain.connect(ctx.destination);
                osc.type = 'sine';
                osc.frequency.value = freq;
                const t = ctx.currentTime + i * 0.13;
                gain.gain.setValueAtTime(coins >= 50 ? 0.35 : 0.25, t);
                gain.gain.exponentialRampToValueAtTime(0.001, t + 0.22);
                osc.start(t); osc.stop(t + 0.22);
            });
        }
    } catch(e) {}
}

function drawSpinWheel(rotation) {
    const canvas = document.getElementById('spin-wheel-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    const cx = W / 2, cy = H / 2;
    const r  = cx - 6;

    ctx.clearRect(0, 0, W, H);

    // Outer glow ring
    ctx.save();
    ctx.shadowColor = '#a855f7'; ctx.shadowBlur = 16;
    ctx.beginPath(); ctx.arc(cx, cy, r + 3, 0, 2 * Math.PI);
    ctx.strokeStyle = '#7c3aed'; ctx.lineWidth = 2.5; ctx.stroke();
    ctx.restore();

    WHEEL_SEGMENTS.forEach((seg, i) => {
        const startA = rotation + i * _WS_ANGLE - Math.PI / 2;
        const endA   = startA + _WS_ANGLE;

        // Gradient fill per segment
        const grd = ctx.createRadialGradient(cx, cy, r * 0.3, cx, cy, r);
        grd.addColorStop(0, seg.altColor);
        grd.addColorStop(1, seg.color);

        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, r, startA, endA);
        ctx.closePath();
        ctx.fillStyle = grd;
        ctx.fill();
        ctx.strokeStyle = 'rgba(0,0,0,0.35)';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Label
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(startA + _WS_ANGLE / 2);
        ctx.textAlign = 'right';
        ctx.fillStyle = seg.textColor;
        ctx.font = `bold 11px 'Segoe UI', sans-serif`;
        const label = seg.coins === 0 ? 'Miss' : `${seg.label} c`;
        ctx.fillText(label, r - 7, 4);
        ctx.restore();
    });

    // Divider lines between segments
    WHEEL_SEGMENTS.forEach((_, i) => {
        const angle = rotation + i * _WS_ANGLE - Math.PI / 2;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(angle) * r, cy + Math.sin(angle) * r);
        ctx.strokeStyle = 'rgba(255,255,255,0.12)';
        ctx.lineWidth = 1;
        ctx.stroke();
    });

    // Center circle
    ctx.beginPath(); ctx.arc(cx, cy, 20, 0, 2 * Math.PI);
    const cGrd = ctx.createRadialGradient(cx, cy, 2, cx, cy, 20);
    cGrd.addColorStop(0, '#a855f7'); cGrd.addColorStop(1, '#1a0a2e');
    ctx.fillStyle = cGrd; ctx.fill();
    ctx.strokeStyle = '#d8b4fe'; ctx.lineWidth = 2; ctx.stroke();

    // Center star/dot
    ctx.fillStyle = '#fff';
    ctx.font = '13px sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText('★', cx, cy);
}

function animateSpinWheel(targetSegIdx, durationMs, onComplete) {
    if (_wheelAnimId) cancelAnimationFrame(_wheelAnimId);

    // Target rotation: put segment center under pointer (top of wheel)
    // drawSpinWheel draws segment i center at: rotation + i*_WS_ANGLE - π/2 + _WS_ANGLE/2
    // For that to equal -π/2 (top, where pointer is):
    //   rotation + center - π/2 = -π/2  →  rotation = -center
    const center     = targetSegIdx * _WS_ANGLE + _WS_ANGLE / 2;
    const baseTarget = -center;

    // Normalize to positive, then add full spins
    const basePos    = ((baseTarget % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
    const curNorm    = ((_wheelRot   % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
    const extra      = (basePos - curNorm + 2 * Math.PI) % (2 * Math.PI);
    const fullSpins  = 5 + Math.floor(Math.random() * 3);
    const targetRot  = _wheelRot + fullSpins * 2 * Math.PI + extra;

    const startRot   = _wheelRot;
    const startTime  = performance.now();
    let   lastTickSeg = -1;

    function easeOut(t) { return 1 - Math.pow(1 - t, 4); }

    function frame(now) {
        const elapsed  = now - startTime;
        const progress = Math.min(elapsed / durationMs, 1);
        _wheelRot = startRot + (targetRot - startRot) * easeOut(progress);
        drawSpinWheel(_wheelRot);

        // Tick sound on each new segment boundary crossed
        const curSeg = Math.floor(
            (((_wheelRot / _WS_ANGLE) % _WS_COUNT) + _WS_COUNT) % _WS_COUNT
        );
        if (curSeg !== lastTickSeg) { _playTick(); lastTickSeg = curSeg; }

        if (progress < 1) {
            _wheelAnimId = requestAnimationFrame(frame);
        } else {
            _wheelRot = targetRot;
            drawSpinWheel(_wheelRot);
            _wheelAnimId = null;
            if (onComplete) onComplete();
        }
    }
    _wheelAnimId = requestAnimationFrame(frame);
}

async function loadSpinStatus() {
    if (!userId) return;
    const card = document.getElementById('spin-card');
    if (!card) return;
    try {
        const cfg = await getFeatureConfig();

        // ── Tournament Lock Mode — applied once per feature-config fetch ──
        // This reuses the existing /get_feature_config call so no extra API
        // request is needed; it covers all tabs and cards in one shot.
        applyTournamentLock(cfg);
        // ─────────────────────────────────────────────────────────────────

        if (!cfg.spin_active) {
            _applyFeatureLock(card, 'spin-lock-overlay', '🎡 Spin Wheel Coming Soon!');
            return;
        }
        _removeFeatureLock(card, 'spin-lock-overlay');

        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_spin_status/${userId}`);
        const data = await res.json();

        const badgeEl  = document.getElementById('spin-count-badge');
        const resultEl = document.getElementById('spin-result');
        const btn      = document.getElementById('spin-btn');

        if (badgeEl) badgeEl.innerText = `${data.spins_done || 0}/${data.spins_total || 5} used`;

        if ((data.spins_left || 0) <= 0) {
            if (btn) {
                btn.disabled    = true;
                btn.innerText   = '✅ All Spins Used Today!';
                btn.style.background = '#334155';
                btn.style.color      = '#94a3b8';
            }
        } else {
            if (btn) {
                btn.disabled    = false;
                btn.innerText   = `🎡 Watch Ad & Spin (${data.spins_left} left)`;
                btn.style.background = 'linear-gradient(135deg,#f1c40f,#f39c12)';
                btn.style.color      = '#000';
            }
        }
    } catch (e) { /* silent */ }
}

async function doSpin() {
    if (!userId) return showToast('User ID not found!', 'error');
    // BUG FIX #1: defense-in-depth — card carries .locked-card while spin is locked.
    const spinCard = document.getElementById('spin-card');
    if (spinCard?.classList.contains('locked-card')) return showToast('🎡 Spin Wheel coming soon!', 'error');
    if (_pendingRequests.has('doSpin')) return;
    _pendingRequests.add('doSpin');

    const btn = document.getElementById('spin-btn');
    if (btn) { btn.disabled = true; btn.innerText = '📺 Loading Ad...'; }

    // Step 1: get token
    let spinToken = null;
    try {
        const tokenRes  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/spin_token/${userId}`, { method: 'POST' });
        const tokenData = await tokenRes.json();
        if (tokenData.status !== 'success' || !tokenData.token) {
            showToast(tokenData.message || 'Could not get spin token.', 'error');
            _pendingRequests.delete('doSpin');
            if (btn) { btn.disabled = false; btn.innerText = '🎡 Watch Ad & Spin'; }
            return;
        }
        spinToken = tokenData.token;
    } catch (e) {
        showToast('⚠️ Server error. Please retry.', 'error');
        _pendingRequests.delete('doSpin');
        if (btn) { btn.disabled = false; btn.innerText = '🎡 Watch Ad & Spin'; }
        return;
    }

    // Step 2: show ad
    if (btn) btn.innerText = '📺 Watching Ad...';
    try {
        await requireAdWatch();
    } catch (e) {
        showToast('📺 Watch the full ad to spin!', 'error');
        _pendingRequests.delete('doSpin');
        if (btn) { btn.disabled = false; btn.innerText = '🎡 Watch Ad & Spin'; }
        return;
    }

    // Step 2b: 10-second cooldown after ad
    await _adCooldown(btn, '🎡 Watch Ad & Spin');

    // Step 3: call API to get reward (server decides the prize)
    if (btn) btn.innerText = '⏳ Getting result...';
    let spinData = null;
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/do_spin/${userId}`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ token: spinToken }),
        });
        spinData = await res.json();
    } catch (e) {
        showToast('⚠️ Error! Please retry.', 'error');
        _pendingRequests.delete('doSpin');
        if (btn) { btn.disabled = false; btn.innerText = '🎡 Watch Ad & Spin'; }
        return;
    }

    if (spinData.status !== 'success') {
        showToast(spinData.message || 'Spin failed.', 'error');
        _pendingRequests.delete('doSpin');
        loadSpinStatus();
        return;
    }

    // Step 4: Animate wheel to land on the winning segment
    const reward   = spinData.reward ?? 0;
    const segIdx   = WHEEL_SEGMENTS.findIndex(s => s.coins === reward);
    const targetSeg = segIdx >= 0 ? segIdx : 0;

    if (btn) { btn.disabled = true; btn.innerText = '🎡 Spinning...'; }

    animateSpinWheel(targetSeg, 4500, () => {
        // Step 5: Play sound + show result after wheel stops
        _playWinSound(reward);

        const resultEl = document.getElementById('spin-result');
        if (resultEl) {
            resultEl.innerText = reward > 0
                ? `🎉 +${reward} coins!`
                : '😅 Miss! Better luck next time!';
            resultEl.style.color   = reward >= 50 ? '#f1c40f' : reward > 0 ? '#2ecc71' : '#94a3b8';
            resultEl.style.display = 'block';
            resultEl.style.animation = 'none';
            void resultEl.offsetWidth;
            resultEl.style.animation = 'spinResultPop 0.5s cubic-bezier(.17,.67,.35,1.3) both';
            setTimeout(() => { if (resultEl) resultEl.style.display = 'none'; }, 4000);
        }

        const toastType = reward > 0 ? 'success' : 'error';
        showToast(spinData.message || (reward > 0 ? `+${reward} coins!` : 'Better luck next time!'), toastType);

        _pendingRequests.delete('doSpin');
        fetchLiveData();
        loadSpinStatus();
    });
}

// ============================================================
// ⛏️ COIN MINING
// ============================================================
let _miningInterval = null;

function _startMiningCountdown(seconds, labelEl, collectBtn, onDone) {
    if (_miningInterval) clearInterval(_miningInterval);
    let secs = Math.max(0, Math.floor(seconds));
    const fmt = n => String(n).padStart(2, '0');

    const tick = () => {
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = secs % 60;
        if (labelEl)    labelEl.innerText    = `⛏️ Mining... ${fmt(h)}:${fmt(m)}:${fmt(s)} remaining`;
        if (collectBtn) collectBtn.innerText = `Collect in ${fmt(h)}:${fmt(m)}:${fmt(s)}`;
    };
    tick();

    _miningInterval = setInterval(() => {
        secs--;
        if (secs <= 0) {
            clearInterval(_miningInterval);
            _miningInterval = null;
            if (labelEl) labelEl.innerText = '✅ Mining Complete! Collect your reward!';
            if (collectBtn) {
                // Reward dynamically from userData (updated by /get_user on load)
                const lvlRew = {1:30, 2:50, 3:75};
                const doneReward = lvlRew[parseInt(userData && userData.mining_level) || 1] || 30;
                collectBtn.disabled  = false;
                collectBtn.innerText = `⛏️ Collect ${doneReward} Coins!`;
                collectBtn.style.background = 'linear-gradient(135deg,#22c55e,#16a34a)';
            }
            if (typeof onDone === 'function') onDone();
        } else {
            tick();
        }
    }, 1000);
}

function _startCooldownCountdown(seconds, watchBtn, labelEl) {
    if (_miningInterval) clearInterval(_miningInterval);
    let cd = Math.max(0, Math.floor(seconds));
    const fmt = n => String(n).padStart(2, '0');

    _miningInterval = setInterval(() => {
        cd--;
        const h = Math.floor(cd / 3600), m = Math.floor((cd % 3600) / 60), s = cd % 60;
        if (watchBtn) watchBtn.innerText = `⏳ Cooldown ${fmt(h)}:${fmt(m)}:${fmt(s)}`;
        if (labelEl)  labelEl.innerText  = `Cooldown active. Wait before mining again.`;
        if (cd <= 0) {
            clearInterval(_miningInterval);
            _miningInterval = null;
            loadMiningStatus();
        }
    }, 1000);
}

async function loadMiningStatus() {
    if (!userId) return;
    const card = document.getElementById('mining-card');
    if (!card) return;

    try {
        const cfg = await getFeatureConfig();

        if (!cfg.mining_active) {
            _applyFeatureLock(card, 'mining-lock-overlay', '⛏️ Coin Mining Coming Soon!');
            return;
        }
        _removeFeatureLock(card, 'mining-lock-overlay');

        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_mining_status/${userId}`);
        const data = await res.json();

        const statusEl  = document.getElementById('mining-status-label');
        const adsEl     = document.getElementById('mining-ads-progress');
        const watchBtn  = document.getElementById('mining-watch-ad-btn');
        const collectBtn = document.getElementById('mining-collect-btn');

        // Reset all
        if (watchBtn)   { watchBtn.style.display   = 'none';  watchBtn.disabled  = false; }
        if (collectBtn) { collectBtn.style.display  = 'none';  collectBtn.disabled = true; }

        // Level & reward — dynamic based on user's current mining level
        const mLevel  = data.mining_level || 1;
        const mReward = data.reward || 30;

        // Update header badge, description & info strip with live values
        const rateBadge = document.getElementById('mining-rate-badge');
        const descRew   = document.getElementById('mining-desc-reward');
        const infoRew   = document.getElementById('mining-info-reward');
        if (rateBadge) rateBadge.innerText = `+${mReward} 🪙/hr`;
        if (descRew)   descRew.innerText   = `${mReward} coins!`;
        if (infoRew)   infoRew.innerText   = `${mReward} Coins`;

        if (data.collect_ready) {
            // Mining done — ready to collect
            if (_miningInterval) { clearInterval(_miningInterval); _miningInterval = null; }
            if (statusEl)   statusEl.innerText = '✅ Mining Complete! Collect your reward!';
            if (collectBtn) {
                collectBtn.style.display    = '';
                collectBtn.disabled         = false;
                collectBtn.innerText        = `⛏️ Collect ${mReward} Coins!`;
                collectBtn.style.background = 'linear-gradient(135deg,#22c55e,#16a34a)';
            }
            if (typeof _updateMiningUpgradeUI === 'function') _updateMiningUpgradeUI(mLevel);

        } else if (data.is_mining) {
            // Mining in progress
            if (statusEl)   statusEl.innerText = '⛏️ Mining in progress...';
            if (typeof _updateMiningUpgradeUI === 'function') _updateMiningUpgradeUI(mLevel);
            if (collectBtn) { collectBtn.style.display = ''; collectBtn.innerText = 'Mining...'; }
            _startMiningCountdown(data.remaining_seconds, statusEl, collectBtn, () => loadMiningStatus());

        } else if (data.cooldown_remaining > 0) {
            // Cooldown
            if (watchBtn) {
                watchBtn.style.display = '';
                watchBtn.disabled      = true;
                watchBtn.innerText     = '⏳ Cooldown...';
            }
            if (statusEl) statusEl.innerText = 'Cooldown active. Please wait before mining again.';
            _startCooldownCountdown(data.cooldown_remaining, watchBtn, statusEl);
            if (typeof _updateMiningUpgradeUI === 'function') _updateMiningUpgradeUI(mLevel);

        } else {
            // Idle — show watch ad button
            const adsLeft = (data.ads_required || 2) - (data.ads_done || 0);
            if (watchBtn) {
                watchBtn.style.display = '';
                watchBtn.disabled      = false;
                watchBtn.innerText     = `📺 Watch Ad ${data.ads_done || 0}/${data.ads_required || 2}`;
            }
            if (statusEl) statusEl.innerText = `Watch ${adsLeft} more ad${adsLeft !== 1 ? 's' : ''} to start mining!`;
            if (typeof _updateMiningUpgradeUI === 'function') _updateMiningUpgradeUI(mLevel);
            if (adsEl)    adsEl.innerText    = `${data.ads_done || 0}/${data.ads_required || 2} ads watched`;
        }
    } catch (e) { /* silent */ }
}

async function watchMiningAd() {
    if (!userId) return showToast('User ID not found!', 'error');
    // BUG FIX #1: defense-in-depth — card carries .locked-card while mining is locked.
    const miningCard = document.getElementById('mining-card');
    if (miningCard?.classList.contains('locked-card')) return showToast('⛏️ Coin Mining coming soon!', 'error');
    if (_pendingRequests.has('miningAd')) return;
    _pendingRequests.add('miningAd');

    const btn = document.getElementById('mining-watch-ad-btn');
    if (btn) { btn.disabled = true; btn.innerText = '📺 Loading Ad...'; }

    // Step 1: get token
    let miningToken = null;
    try {
        const tokenRes  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/mining_ad_token/${userId}`, { method: 'POST' });
        const tokenData = await tokenRes.json();

        if (tokenData.status === 'cooldown' || tokenData.status === 'mining') {
            showToast(tokenData.message, 'error');
            _pendingRequests.delete('miningAd');
            loadMiningStatus();
            return;
        }
        if (tokenData.status !== 'success' || !tokenData.token) {
            showToast(tokenData.message || 'Could not start mining ad.', 'error');
            _pendingRequests.delete('miningAd');
            if (btn) { btn.disabled = false; btn.innerText = '📺 Watch Ad'; }
            return;
        }
        miningToken = tokenData.token;
    } catch (e) {
        showToast('⚠️ Server error.', 'error');
        _pendingRequests.delete('miningAd');
        if (btn) { btn.disabled = false; btn.innerText = '📺 Watch Ad'; }
        return;
    }

    // Step 2: show ad
    if (btn) btn.innerText = '📺 Watching Ad...';
    try {
        await requireAdWatch();
    } catch (e) {
        showToast('📺 Watch the full ad to start mining!', 'error');
        _pendingRequests.delete('miningAd');
        if (btn) { btn.disabled = false; btn.innerText = '📺 Watch Ad'; }
        return;
    }

    // Step 2b: 10-second cooldown after ad
    await _adCooldown(btn, '📺 Watch Ad');

    // Step 3: send token → start_mining
    if (btn) btn.innerText = 'Processing...';
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/start_mining/${userId}`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ token: miningToken }),
        });
        const data = await res.json();

        if (data.status === 'mining_started') {
            const lvlRewards = {1:30, 2:50, 3:75};
            const startReward = lvlRewards[data.mining_level || 1] || 30;
            showToast(`⛏️ Mining started! Come back in 1 hour to collect ${startReward} coins!`, 'success');
        } else if (data.status === 'ad_counted') {
            showToast(data.message || 'Ad counted! Watch more to start mining.', 'success');
        } else {
            showToast(data.message || 'Error. Please retry.', 'error');
        }
    } catch (e) {
        showToast('⚠️ Error! Please retry.', 'error');
    } finally {
        _pendingRequests.delete('miningAd');
        loadMiningStatus();
    }
}

async function collectMining() {
    if (!userId) return showToast('User ID not found!', 'error');
    if (_pendingRequests.has('collectMining')) return;
    _pendingRequests.add('collectMining');

    const btn = document.getElementById('mining-collect-btn');
    if (btn) { btn.disabled = true; btn.innerText = 'Collecting...'; }

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/collect_mining/${userId}`, { method: 'POST' });
        const data = await res.json();

        if (data.status === 'success') {
            showToast(`⛏️ +${data.reward} coins collected! 🪙`, 'success');
            if (_miningInterval) { clearInterval(_miningInterval); _miningInterval = null; }
            fetchLiveData();
            loadMiningStatus();
        } else if (data.status === 'not_ready') {
            showToast(data.message, 'error');
            loadMiningStatus();
        } else {
            showToast(data.message || 'Could not collect.', 'error');
            if (btn) { btn.disabled = false; loadMiningStatus(); }
        }
    } catch (e) {
        showToast('⚠️ Error! Please retry.', 'error');
        if (btn) { btn.disabled = false; loadMiningStatus(); }
    } finally {
        _pendingRequests.delete('collectMining');
    }
}

// ============================================================
// 💣 BOMB BOX CHALLENGE
// ============================================================

let _bombBoxCooldownInterval = null;
let _activeBombGameId        = null;

function _startBombBoxCooldown(seconds) {
    if (_bombBoxCooldownInterval) clearInterval(_bombBoxCooldownInterval);
    let cd = Math.max(0, Math.floor(seconds));
    const fmt = n => String(n).padStart(2, '0');
    const btn    = document.getElementById('bomb-box-ad-btn');
    const status = document.getElementById('bomb-box-status');

    const tick = () => {
        const m = Math.floor(cd / 60), s = cd % 60;
        if (btn)    { btn.disabled = true; btn.innerText = `⏳ Cooldown ${fmt(m)}:${fmt(s)}`; }
        if (status) status.innerText = `⏳ Next game in ${fmt(m)}:${fmt(s)}`;
    };
    tick();

    _bombBoxCooldownInterval = setInterval(() => {
        cd--;
        if (cd <= 0) {
            clearInterval(_bombBoxCooldownInterval);
            _bombBoxCooldownInterval = null;
            loadBombBoxStatus();
        } else tick();
    }, 1000);
}

// ============================================================
// 🌐 WEB TASKS LOCK
// ============================================================
async function loadWebTasksStatus() {
    if (!userId) return;
    const card = document.getElementById('web-tasks-card');
    if (!card) return;
    try {
        const cfg = await getFeatureConfig();
        if (!cfg.web_tasks_active) {
            if (getComputedStyle(card).position === 'static') card.style.position = 'relative';
            card.style.overflow = 'hidden';
            _applyFeatureLock(card, 'web-tasks-lock-overlay', '🌐 Web Tasks Coming Soon!');
            card.style.pointerEvents = 'none';
            card.style.cursor = 'default';
        } else {
            _removeFeatureLock(card, 'web-tasks-lock-overlay');
            card.style.pointerEvents = '';
            card.style.cursor = '';
        }
    } catch (e) { /* ignore */ }
}

// ============================================================
// 💎 PREMIUM CARD LOCK
// ============================================================
async function loadPremiumCardStatus() {
    if (!userId) return;
    const card = document.getElementById('premium-buy-card');
    if (!card) return;
    // Don't show the "Coming Soon" lock over a user who already has active premium —
    // that lock is only meant to block NEW purchases, not hide existing status.
    const alreadyPremium = !!(userData && userData.premium_info && userData.premium_info.premium);
    try {
        const cfg = await getFeatureConfig();
        if (!cfg.premium_active && !alreadyPremium) {
            if (card.style.position !== 'relative') card.style.position = 'relative';
            card.style.overflow = 'hidden';
            _applyFeatureLock(card, 'premium-lock-overlay', '💎 Premium Coming Soon!');
            card.style.pointerEvents = 'none';
            card.style.cursor = 'default';
        } else {
            _removeFeatureLock(card, 'premium-lock-overlay');
            card.style.pointerEvents = '';
            card.style.cursor = 'pointer';
        }
    } catch (e) { /* ignore */ }
}

// ============================================================
// 💎 VIP TASKS
// ============================================================
async function loadVipTasks() {
    if (!userId) return;
    const section = document.getElementById('vip-tasks-section');
    const list    = document.getElementById('vip-tasks-list');
    if (!section || !list) return;

    const isPrem = !!(userData && userData.premium_info && userData.premium_info.premium);
    if (!isPrem) { section.style.display = 'none'; return; }
    section.style.display = 'block';

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_vip_tasks?user_id=${userId}`);
        const data = await res.json();
        const tasks   = data.tasks   || [];
        const claimed = data.claimed || [];

        if (!tasks.length) {
            list.innerHTML = '<p style="color:#475569;text-align:center;font-size:13px;padding:10px 0;">No VIP tasks available yet.</p>';
            return;
        }
        list.innerHTML = '';
        tasks.forEach(task => {
            const done    = claimed.includes(task.task_id);
            const taskUrl = task.url || null;
            const card    = document.createElement('div');
            card.className = 'promo-task-card' + (done ? ' completed' : '');
            card.style.border = '1px solid rgba(29,155,240,0.3)';
            card.innerHTML = `
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;">
                    <p style="font-size:13px;font-weight:700;color:#38bdf8;margin:0;">💎 ${task.title}</p>
                    <span style="font-size:12px;color:#f1c40f;font-weight:700;white-space:nowrap;margin-left:8px;">+${task.reward||20} 🪙</span>
                </div>
                ${taskUrl ? `
                <a href="${taskUrl}" target="_blank" rel="noopener noreferrer"
                    style="display:flex;align-items:center;justify-content:center;gap:6px;
                           width:100%;padding:8px 0;margin-bottom:8px;
                           background:rgba(56,189,248,0.10);border:1px solid rgba(56,189,248,0.30);
                           border-radius:10px;color:#38bdf8;font-size:12px;font-weight:700;
                           text-decoration:none;box-sizing:border-box;">
                    🔗 Go to Task
                </a>` : ''}
                <button class="promo-btn vip-task-claim-btn" data-task-id="${escapeHtml(task.task_id)}" onclick="claimVipTask('${task.task_id}')" ${done ? 'disabled' : ''}
                    style="background:${done ? '' : 'linear-gradient(135deg,#1a78c2,#1D9BF0)'};">
                    ${done ? '✅ Claimed' : '💎 Claim Reward'}
                </button>`;
            list.appendChild(card);
        });
    } catch(e) {
        list.innerHTML = '<p style="color:#475569;text-align:center;font-size:13px;">Failed to load VIP tasks.</p>';
    }
}

async function claimVipTask(taskId) {
    if (!userId) return showToast('User ID not found!', 'error');
    const reqKey = `vip_task_${taskId}`;
    if (_pendingRequests.has(reqKey)) return;
    _pendingRequests.add(reqKey);

    const btn = Array.from(document.querySelectorAll('.vip-task-claim-btn'))
        .find(el => el.dataset.taskId === String(taskId));
    if (btn) {
        btn.disabled = true;
        btn.innerText = '📺 Watching Ad...';
    }

    try {
        // VIP task rewards are ad-gated like the other earning actions.
        // requireAdWatch() handles the configured Monetag SDK and rejects
        // unless the ad reports a valued/rewarded completion.
        await requireAdWatch();
        if (btn) btn.innerText = 'Claiming Reward...';

        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/claim_vip_task`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({user_id: userId, task_id: taskId}),
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast(data.message || '✅ VIP Task claimed!', 'success');
            setTimeout(loadVipTasks, 800);
            setTimeout(fetchLiveData, 1000);
        } else {
            showToast(data.message || 'Error claiming task.', 'error');
        }
    } catch(e) {
        showToast(
            e?.message === 'ad_skipped'
                ? 'Watch the full ad to claim this VIP task.'
                : 'Ad could not be completed. No reward was claimed.',
            'error'
        );
        if (btn) {
            btn.disabled = false;
            btn.innerText = '💎 Claim Reward';
        }
    } finally {
        _pendingRequests.delete(reqKey);
    }
}

// ============================================================
// ⚡ MINING SPEED UPGRADE
// ============================================================
async function upgradeMining() {
    if (!userId) return showToast('User ID not found!', 'error');
    const btn = document.getElementById('mining-upgrade-btn');
    if (btn) { btn.disabled = true; btn.innerText = '⏳ Upgrading...'; }
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/upgrade_mining/${userId}`, { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
            showToast(data.message || '⚡ Mining upgraded!', 'success');
            setTimeout(() => { loadMiningStatus(); fetchLiveData(); }, 800);
        } else {
            showToast(data.message || 'Upgrade failed.', 'error');
            if (btn) { btn.disabled = false; btn.innerText = btn.innerText.replace('⏳ Upgrading...', '⬆️ Upgrade'); }
        }
    } catch(e) {
        showToast('⚠️ Server error.', 'error');
        if (btn) { btn.disabled = false; }
    }
}

function _updateMiningUpgradeUI(level) {
    const badge = document.getElementById('mining-level-badge');
    const btn   = document.getElementById('mining-upgrade-btn');
    const REWARDS = {1:30, 2:50, 3:75};
    const COSTS   = {2:200, 3:500};
    const reward  = REWARDS[level] || 30;
    if (badge) badge.innerText = `Level ${level} • ${reward}🪙`;
    if (btn) {
        if (level >= 3) {
            btn.innerText   = '🏆 Max Level Reached!';
            btn.disabled    = true;
            btn.style.background = 'rgba(255,255,255,0.05)';
            btn.style.color      = '#6e7e96';
        } else {
            const nextLvl  = level + 1;
            const cost     = COSTS[nextLvl] || 500;
            const nextRew  = REWARDS[nextLvl] || 75;
            btn.innerText  = `⬆️ Upgrade to Level ${nextLvl} (${nextRew}🪙) — ${cost} Coins`;
            btn.disabled   = false;
            btn.style.background = 'linear-gradient(135deg,#1e3a5f,#2563eb)';
            btn.style.color      = '#fff';
        }
    }
    // Update profile tab too
    const plv = document.getElementById('profile-mining-level');
    const prw = document.getElementById('profile-mining-reward');
    if (plv) plv.innerText = `Level ${level}`;
    if (prw) prw.innerText = `Earning ${reward} coins per session`;

    // Mining level dots update
    const dots = document.querySelectorAll('#profile-mining-dots .mining-lvl-dot');
    dots.forEach((dot, i) => {
        if (i < level) dot.classList.add('on');
        else           dot.classList.remove('on');
    });
}

// ============================================================
// 👤 PROFILE TAB
// ============================================================
const _RANK_TIERS = [
    { min:0,      max:999,   label:'🥉 Beginner',    emoji:'🥉', color:'#94a3b8' },
    { min:1000,   max:4999,  label:'🥈 Bronze',       emoji:'🥈', color:'#cd7f32' },
    { min:5000,   max:14999, label:'🥇 Silver',       emoji:'🥇', color:'#94a3b8' },
    { min:15000,  max:39999, label:'💎 Gold',         emoji:'💎', color:'#f1c40f' },
    { min:40000,  max:99999, label:'👑 Platinum',     emoji:'👑', color:'#38bdf8' },
    { min:100000, max:Infinity, label:'🌟 Legend',   emoji:'🌟', color:'#a78bfa' },
];

function loadProfileTab() {
    // BUG FIX: allow rendering for blocked users too (backend now sends
    // profile fields alongside status:"blocked") — see showBlockedView().
    if (!userData || (userData.status !== 'success' && userData.status !== 'blocked')) return;
    const d        = userData;
    const coins    = d.coins || 0;
    const refCount = getRefCount(d.referrals);
    const streak   = d.streak_day || 0;
    // total_ads_today = earn + spin + mining ads (main.py sends this after patch)
    const ads      = (d.total_ads_today != null ? d.total_ads_today : d.ads_today) || 0;
    const premInfo = d.premium_info || {};
    const isPrem   = !!premInfo.premium;
    const uname    = d.username || d.first_name || 'User';
    const uid      = userId || '—';

    // Avatar
    const av = document.getElementById('profile-avatar');
    if (av) av.innerText = uname.charAt(0).toUpperCase() || '?';

    // Name + ID
    const un = document.getElementById('profile-username');
    if (un) un.innerText = uname;
    const ui = document.getElementById('profile-userid');
    if (ui) ui.innerText = `ID: ${uid}`;

    // Premium badge
    const pb = document.getElementById('profile-premium-badge');
    if (pb) {
        if (isPrem) {
            pb.innerHTML = `<span style="display:inline-flex;align-items:center;gap:5px;background:linear-gradient(135deg,#1a78c2,#1D9BF0);color:#fff;font-size:11px;font-weight:800;padding:3px 10px;border-radius:20px;">✔ PREMIUM · ${premInfo.days_left||0}d left</span>`;
            pb.style.display = 'block';
        } else {
            pb.innerHTML = `<span style="font-size:11px;color:#6e7e96;">Free Account</span>`;
            pb.style.display = 'block';
        }
    }

    // Stats
    const pc = document.getElementById('profile-coins');     if (pc) pc.innerText = coins.toLocaleString();
    const pr = document.getElementById('profile-referrals'); if (pr) pr.innerText = refCount;
    const ps = document.getElementById('profile-streak');    if (ps) ps.innerText = streak;
    const pa = document.getElementById('profile-ads');       if (pa) pa.innerText = ads;

    // Rank
    const tier = _RANK_TIERS.find(t => coins >= t.min && coins <= t.max) || _RANK_TIERS[0];
    const nextTier = _RANK_TIERS[_RANK_TIERS.indexOf(tier) + 1];
    const rl = document.getElementById('profile-rank-label'); if (rl) { rl.innerText = tier.label; rl.style.color = tier.color; }
    const re = document.getElementById('profile-rank-emoji'); if (re) re.innerText = tier.emoji;
    const rb = document.getElementById('profile-rank-bar');
    if (rb) {
        const pct = nextTier ? Math.min(((coins - tier.min) / (nextTier.min - tier.min)) * 100, 100) : 100;
        rb.style.width = pct + '%';
    }
    const rs = document.getElementById('profile-rank-sub');
    if (rs) rs.innerText = nextTier ? `${(nextTier.min - coins).toLocaleString()} coins to ${nextTier.label}` : '🌟 Max Rank Achieved!';

    // Mining level dots
    const mDots = document.querySelectorAll('#profile-mining-dots .mining-lvl-dot');
    const mLvl  = parseInt(d.mining_level) || 1;
    mDots.forEach((dot, i) => {
        if (i < mLvl) dot.classList.add('on');
        else          dot.classList.remove('on');
    });

    // Tournament participation count
    const pt = document.getElementById('profile-tournaments');
    if (pt) pt.innerText = d.tournament_count != null ? d.tournament_count : '—';
}

async function loadBombBoxStatus() {
    if (!userId) return;
    const card = document.getElementById('bomb-box-card');
    if (!card) return;

    try {
        const cfg = await getFeatureConfig();

        if (!cfg.bomb_box_active) {
            _applyFeatureLock(card, 'bomb-lock-overlay', '💣 Bomb Box Coming Soon!');
            return;
        }
        _removeFeatureLock(card, 'bomb-lock-overlay');

        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/bomb_box_status/${userId}`);
        const data = await res.json();

        const btn    = document.getElementById('bomb-box-ad-btn');
        const grid   = document.getElementById('bomb-box-grid');
        const status = document.getElementById('bomb-box-status');
        const result = document.getElementById('bomb-box-result');

        if (grid)   grid.style.display   = 'none';
        if (result) result.style.display = 'none';

        if (data.cooldown_remaining > 0) {
            if (btn) btn.style.display = '';
            _startBombBoxCooldown(data.cooldown_remaining);
        } else if (data.active_game_id) {
            _activeBombGameId = data.active_game_id;
            if (btn)    btn.style.display  = 'none';
            if (grid)   grid.style.display = 'grid';
            if (status) status.innerText   = '🎯 Pick a box! One has a bomb 💣';
            for (let i = 0; i < 4; i++) {
                const b = document.getElementById(`bb-btn-${i}`);
                if (b) { b.disabled = false; b.innerText = `📦 Box ${i + 1}`; b.style.background = ''; b.style.color = ''; }
            }
        } else {
            _activeBombGameId = null;
            if (_bombBoxCooldownInterval) { clearInterval(_bombBoxCooldownInterval); _bombBoxCooldownInterval = null; }
            if (btn) {
                btn.style.display    = '';
                btn.disabled         = false;
                btn.innerText        = '📺 Watch Ad to Play';
                btn.style.background = 'linear-gradient(135deg,#ef4444,#b91c1c)';
            }
            if (status) status.innerText = 'Watch 1 ad → Pick a box → Win coins!';
        }
    } catch (e) { /* silent */ }
}

async function watchBombBoxAd() {
    if (!userId) return showToast('User ID not found!', 'error');
    // BUG FIX #1: defense-in-depth — card carries .locked-card while bomb box is locked.
    const bombCard = document.getElementById('bomb-box-card');
    if (bombCard?.classList.contains('locked-card')) return showToast('💣 Bomb Box coming soon!', 'error');
    if (_pendingRequests.has('bombBoxAd')) return;
    _pendingRequests.add('bombBoxAd');

    const btn    = document.getElementById('bomb-box-ad-btn');
    const status = document.getElementById('bomb-box-status');

    if (btn) { btn.disabled = true; btn.innerText = '📺 Loading Ad...'; }

    // Step 1 — get ad token
    let bombToken = null;
    try {
        const tokenRes  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/bomb_box_token/${userId}`, { method: 'POST' });
        const tokenData = await tokenRes.json();
        if (tokenData.status === 'cooldown') {
            showToast(tokenData.message, 'error');
            _pendingRequests.delete('bombBoxAd');
            loadBombBoxStatus();
            return;
        }
        if (tokenData.status !== 'success' || !tokenData.token) {
            showToast(tokenData.message || 'Could not start game.', 'error');
            _pendingRequests.delete('bombBoxAd');
            if (btn) { btn.disabled = false; btn.innerText = '📺 Watch Ad to Play'; }
            return;
        }
        bombToken = tokenData.token;
    } catch (e) {
        showToast('⚠️ Server error.', 'error');
        _pendingRequests.delete('bombBoxAd');
        if (btn) { btn.disabled = false; btn.innerText = '📺 Watch Ad to Play'; }
        return;
    }

    // Step 2 — show ad
    if (btn) btn.innerText = '📺 Watching Ad...';
    try {
        await requireAdWatch();
    } catch (e) {
        showToast('📺 Watch the full ad to play!', 'error');
        _pendingRequests.delete('bombBoxAd');
        if (btn) { btn.disabled = false; btn.innerText = '📺 Watch Ad to Play'; }
        return;
    }

    // Step 2b — 10s post-ad cooldown
    await _adCooldown(btn, '📺 Watch Ad to Play');

    // Step 3 — start game
    if (btn) { btn.disabled = true; btn.innerText = '⏳ Starting Game...'; }
    if (status) status.innerText = 'Creating your game...';

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/bomb_box_start/${userId}`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ token: bombToken }),
        });
        const data = await res.json();

        if (data.status !== 'success' || !data.game_id) {
            showToast(data.message || 'Could not start game.', 'error');
            _pendingRequests.delete('bombBoxAd');
            loadBombBoxStatus();
            return;
        }

        _activeBombGameId = data.game_id;

        const grid = document.getElementById('bomb-box-grid');
        if (btn)    btn.style.display  = 'none';
        if (grid)   grid.style.display = 'grid';
        if (status) status.innerText   = '🎯 Pick a box! One has a bomb 💣';

        for (let i = 0; i < 4; i++) {
            const b = document.getElementById(`bb-btn-${i}`);
            if (b) {
                b.disabled         = false;
                b.innerText        = `📦 Box ${i + 1}`;
                b.style.background = 'linear-gradient(135deg,#3b1212,#7f1d1d)';
                b.style.color      = '#fca5a5';
            }
        }
    } catch (e) {
        showToast('⚠️ Server error.', 'error');
        loadBombBoxStatus();
    }
    _pendingRequests.delete('bombBoxAd');
}

async function pickBombBox(index) {
    if (!userId)            return showToast('User ID not found!', 'error');
    if (!_activeBombGameId) return showToast('No active game! Click "Watch Ad to Play" first.', 'error');
    if (_pendingRequests.has('bombPick')) return;
    _pendingRequests.add('bombPick');

    // Disable all boxes immediately
    for (let i = 0; i < 4; i++) {
        const b = document.getElementById(`bb-btn-${i}`);
        if (b) { b.disabled = true; if (i === index) b.innerText = '⏳'; }
    }

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/bomb_box_pick/${userId}`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ game_id: _activeBombGameId, box_index: index }),
        });
        const data = await res.json();

        if (data.status !== 'success') {
            showToast(data.message || 'Error!', 'error');
            _pendingRequests.delete('bombPick');
            loadBombBoxStatus();
            return;
        }

        // Reveal all boxes with result
        if (data.reveal) {
            data.reveal.forEach(box => {
                const b = document.getElementById(`bb-btn-${box.index}`);
                if (!b) return;
                const isPicked = box.index === data.picked;
                if (box.type === 'bomb') {
                    b.innerText          = '💣';
                    b.style.background   = isPicked ? 'linear-gradient(135deg,#7f1d1d,#991b1b)' : 'linear-gradient(135deg,#1e293b,#334155)';
                    b.style.color        = '#fca5a5';
                } else {
                    b.innerText          = `✅ ${box.value}🪙`;
                    b.style.background   = isPicked ? 'linear-gradient(135deg,#14532d,#166534)' : 'linear-gradient(135deg,#1e293b,#334155)';
                    b.style.color        = isPicked ? '#86efac' : '#64748b';
                }
            });
        }

        // Show result banner
        const isWin    = data.result === 'reward';
        const resultEl = document.getElementById('bomb-box-result');
        if (resultEl) {
            resultEl.innerHTML = isWin
                ? `<span style="font-size:22px;">🎉</span><br><b style="color:#22c55e;">+${data.coins_won} coins!</b><br><span style="font-size:12px;color:#94a3b8;">${data.message || ''}</span>`
                : `<span style="font-size:22px;">💣</span><br><b style="color:#ef4444;">BOOM! Better luck next time!</b><br><span style="font-size:12px;color:#94a3b8;">${data.message || ''}</span>`;
            resultEl.style.display    = '';
            resultEl.style.background = isWin ? 'rgba(34,197,94,0.08)'  : 'rgba(239,68,68,0.08)';
            resultEl.style.border     = `1px solid ${isWin ? '#22c55e' : '#ef4444'}`;
        }

        const statusEl = document.getElementById('bomb-box-status');
        if (statusEl) statusEl.innerText = isWin ? `🎉 +${data.coins_won} coins added!` : '💣 Boom! Try again in 15 minutes.';

        showToast(isWin ? `🎉 +${data.coins_won} coins!` : '💣 Boom! Better luck next time!', isWin ? 'success' : 'error');

        _activeBombGameId = null;
        fetchLiveData();

        // After 2s hide grid, show cooldown
        setTimeout(() => {
            const grid = document.getElementById('bomb-box-grid');
            const btn  = document.getElementById('bomb-box-ad-btn');
            if (grid) grid.style.display = 'none';
            if (btn)  btn.style.display  = '';
            _startBombBoxCooldown(BOMB_BOX_COOLDOWN_SECS);
        }, 2000);

    } catch (e) {
        showToast('⚠️ Server error. Try again.', 'error');
        loadBombBoxStatus();
    }
    _pendingRequests.delete('bombPick');
}

// ============================================================
// BALANCE REFRESH
// ============================================================
async function refreshBalance() {
    if (!userId) return;
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_user/${userId}`);
        const data = await res.json();
        if (data.status === 'success') {
            const balEl = document.getElementById('balance');
            if (balEl) balEl.innerText = `${data.coins || 0} 🪙`;
            userData = data;
            updateAllBonusUI(data);
        }
    } catch (e) { /* silent */ }
}

// ============================================================
// LEADERBOARD
// ============================================================
// BUG FIX #3: refreshLeaderboard — show loading immediately, handle Render cold starts
async function refreshLeaderboard() {
    const list = document.getElementById('leaderboard-list');
    if (!list) return;

    const hasCachedData = userData && userData.leaderboard && userData.leaderboard !== "none";

    // Immediately show cached data if available, otherwise show a loading spinner
    if (hasCachedData) {
        updateLeaderboardUI(userData.leaderboard);
    } else {
        list.innerHTML = '<div style="text-align:center;padding:24px;"><p style="color:#6e7e96;font-size:13px;">⏳ Loading leaderboard...</p></div>';
    }

    // Fetch fresh data — with one automatic retry for Render cold starts
    const _tryFetch = async () => {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_leaderboard`, {}, 3, 3000);
        const data = await res.json();
        return data;
    };

    try {
        let data;
        try {
            data = await _tryFetch();
        } catch (_firstErr) {
            // Render cold start — wait 4s and try once more
            await new Promise(r => setTimeout(r, 4000));
            data = await _tryFetch();
        }

        if (data.status === "success" && data.leaderboard) {
            // Persist fresh data to cache so tab switching stays fast
            if (userData) userData.leaderboard = data.leaderboard;
            updateLeaderboardUI(data.leaderboard);
        } else if (!hasCachedData) {
            list.innerHTML = '<div style="text-align:center;padding:24px;"><p style="color:#6e7e96;font-size:13px;margin-bottom:12px;">🏆 No rankings yet. Earn coins and climb to the top!</p></div>';
        }
    } catch (e) {
        // Only show error when there's nothing cached to display
        if (!hasCachedData) {
            list.innerHTML = `<div style="text-align:center;padding:24px;">
                <p style="color:#ef4444;font-size:13px;margin-bottom:12px;">⚠️ Load nahi hua. Retry karo.</p>
                <button onclick="refreshLeaderboard()" style="background:rgba(212,160,23,0.15);border:1px solid rgba(212,160,23,0.3);color:var(--gold);border-radius:10px;padding:8px 20px;font-size:13px;font-weight:700;cursor:pointer;">🔄 Retry</button>
            </div>`;
        }
    }
}

function updateLeaderboardUI(leaderboardData) {
    const list = document.getElementById('leaderboard-list');
    if (!list) return;
    if (!leaderboardData || leaderboardData === "none") {
        list.innerHTML = "<p class='spinner'>No users yet.</p>";
        return;
    }
    const medals  = ['🥇', '🥈', '🥉'];
    const players = leaderboardData.split('|');
    list.innerHTML = players.map((p, i) => {
        // SECURITY FIX: backend now sends "user_id:coins:username" — user_id
        // is only used internally to detect "isMe" and is never rendered.
        // Previously this showed "User <raw telegram id>" for every other
        // player, publicly leaking real Telegram IDs to anyone opening the
        // app (no login required to view the leaderboard).
        const [id, coins] = p.split(':');
        const isMe   = String(id) === String(userId);
        // Generic rank-based label only — no username or ID shown for anyone
        // but the viewer themself. @username is directly searchable/messageable
        // on Telegram, so showing it is arguably worse for privacy than the
        // raw numeric ID it replaced; a plain "Player #N" still lets the
        // leaderboard do its job (show relative ranking) without exposing
        // who anyone actually is.
        const label = isMe ? '👤 You' : `Player #${i + 1}`;
        return `
            <div class="lb-item" style="${isMe ? 'background:rgba(99,102,241,0.1);border-radius:8px;padding:10px;' : ''}">
                <span class="lb-rank">${medals[i] || `#${i + 1}`}</span>
                <span class="lb-user">${label}</span>
                <span class="lb-coins">${parseInt(coins) || 0} 🪙</span>
            </div>`;
    }).join('');
}

// ============================================================
// PROMO CODE
// ============================================================
async function redeemPromo() {
    if (!userId) return showToast("User ID not found!", "error");
    const inputEl = document.getElementById('promo-input');
    const code    = inputEl ? inputEl.value.trim().toUpperCase() : '';
    if (!code) return showToast("Please enter a promo code!", "error");

    if (_pendingRequests.has('redeemPromo')) return;
    _pendingRequests.add('redeemPromo');

    const btn = document.getElementById('promo-btn');
    if (btn) { btn.disabled = true; btn.innerText = "Checking..."; }

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/redeem_promo`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ user_id: userId, code })
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast(`🎉 ${data.message}`, "success");
            if (inputEl) inputEl.value = '';
            fetchLiveData();
        } else {
            showToast(data.message || "Invalid promo code.", "error");
        }
    } catch (e) {
        showToast("⚠️ Connection error. Please retry.", "error");
    } finally {
        _pendingRequests.delete('redeemPromo');
        if (btn) { btn.disabled = false; btn.innerText = "Redeem"; }
    }
}

// ============================================================
// WITHDRAW
// ============================================================
let _selectedWithdrawMethod = 'upi';

function selectWithdrawMethod(method) {
    _selectedWithdrawMethod = method;
    const colors = { upi: '#2ecc71', google: '#f59e0b' };
    const bg     = { upi: '#0d2318', google: '#1c1600' };
    ['upi', 'google'].forEach(m => {
        const btn   = document.getElementById(`method-btn-${m}`);
        const panel = document.getElementById(`method-input-${m}`);
        if (btn) {
            const active = m === method;
            btn.style.borderColor = active ? colors[m] : '#334155';
            btn.style.color       = active ? colors[m] : '#94a3b8';
            btn.style.background  = active ? bg[m]     : '#0f2027';
        }
        if (panel) panel.style.display = m === method ? '' : 'none';
    });
}

async function requestWithdraw() {
    if (!userId) return showToast("User ID not found!", "error");
    if (_pendingRequests.has('withdraw')) return showToast("Request already in progress...", "error");

    const amountEl   = document.getElementById('withdraw-amount');
    const rawAmount  = amountEl ? amountEl.value.trim() : '';
    const reqAmount  = parseInt(rawAmount);
    const totalCoins = userData.coins || 0;
    const refCount   = getRefCount(userData.referrals);
    const method     = _selectedWithdrawMethod;

    if (!rawAmount)                     return showToast("Please enter the coin amount!", "error");
    if (isNaN(reqAmount))               return showToast("Please enter a valid number!", "error");
    if (reqAmount <= 0)                 return showToast("Amount cannot be zero or negative!", "error");
    const _minWdCheck = window._dynamicMinWithdraw || MIN_WITHDRAW_COINS;
    if (reqAmount < _minWdCheck) return showToast(`Minimum ${_minWdCheck} coins required.${_minWdCheck < MIN_WITHDRAW_COINS ? ' (Premium)' : ''}`, "error");
    if (reqAmount > totalCoins)         return showToast(`Insufficient balance. You have ${totalCoins} coins.`, "error");

    // BUG FIX #2: Premium user ke liye sirf 2 referrals chahiye, free user ke liye 5.
    // Pehle hardcoded 5 tha — premium users ke 3-4 referrals hone par bhi block ho jaate the.
    if (CONFIG.REFERRAL_ACTIVE === true) {
        const isPremForRef = !!(userData && userData.premium_info && userData.premium_info.premium);
        const _refNeeded   = isPremForRef ? 2 : 5;
        if (refCount < _refNeeded) {
            return showToast(`You need ${_refNeeded - refCount} more referral(s) to unlock withdrawal.`, "error");
        }
    }

    let paymentAddress = '';
    if (method === 'upi') {
        const upi = document.getElementById('upi-id')?.value.trim();
        if (!upi || !upi.includes('@')) return showToast("Please enter a valid UPI ID! (Example: name@upi)", "error");
        paymentAddress = upi;
    } else if (method === 'google') {
        paymentAddress = 'via_telegram';
    }

    _pendingRequests.add('withdraw');
    const btn = document.querySelector('[onclick="requestWithdraw()"]');
    if (btn) { btn.disabled = true; btn.innerText = "Processing..."; }

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/withdraw`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({
                user_id:         userId,
                method,
                payment_address: paymentAddress,
                upi_id:          method === 'upi' ? paymentAddress : undefined,
                amount:          reqAmount,
            }),
        });
        const data = await res.json();
        if (data.status === "success") {
            const methodLabel = method === 'upi' ? 'UPI' : 'Google Play';
            showToast(`💸 ${methodLabel} withdrawal request submitted!`, "success");
            if (amountEl) amountEl.value = '';
            const upiEl = document.getElementById('upi-id');
            if (upiEl) upiEl.value = '';
            fetchLiveData();
        } else {
            showToast(data.message || "An error occurred. Please retry.", "error");
        }
    } catch (e) {
        showToast("⚠️ Connection error! Please retry.", "error");
    } finally {
        _pendingRequests.delete('withdraw');
        if (btn) { btn.disabled = false; btn.innerText = "Withdraw Now"; }
    }
}

// ============================================================
// TASKS
// ============================================================
function openTask(taskKey, type) {
    const link = type === 'yt'      ? CONFIG.YT_LINKS[taskKey]
               : type === 'partner' ? CONFIG.PARTNER_LINKS?.[taskKey]
               : CONFIG.WEB_LINKS[taskKey];
    if (link && link !== '#') {
        openExternalLink(link);
    } else {
        showToast("Link will be updated soon!", "error");
    }
}

async function verifyTask(taskId, inputId, sponsorLink) {
    const code = document.getElementById(inputId)?.value.trim();
    if (!code) return showToast("Please enter the code!", "error");

    const reqKey = `verify_${taskId}`;
    if (_pendingRequests.has(reqKey)) return;
    _pendingRequests.add(reqKey);

    let linkToSend = sponsorLink || "";
    if (!linkToSend && CONFIG.SPONSORS?.[taskId]) linkToSend = CONFIG.SPONSORS[taskId].link || "";

    const verifyBtn = document.querySelector(`[data-verify-btn="${taskId}"]`)
                   || document.querySelector(`[onclick^="verifyTask('${taskId}'"]`);
    if (verifyBtn) verifyBtn.disabled = true;

    startCountdown(10,
        (s) => { if (verifyBtn) verifyBtn.innerText = `Wait ${s}s...`; },
        async () => {
            if (verifyBtn) { verifyBtn.disabled = true; verifyBtn.innerText = '📺 Watch Ad...'; }
            try {
                await requireAdWatch();
            } catch (e) {
                showToast('📺 Watch the full ad to claim your reward!', 'error');
                if (verifyBtn) { verifyBtn.disabled = false; verifyBtn.innerText = 'Verify'; }
                _pendingRequests.delete(reqKey);
                return;
            }
            if (verifyBtn) verifyBtn.innerText = 'Verifying...';
            try {
                const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/verify_task`, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ user_id: userId, task_id: taskId, code, link: linkToSend })
                });
                const data = await res.json();
                if (data.status === "success") {
                    showToast(`✅ ${data.message}`, "success");
                    // BUG FIX: this used to only call fetchLiveData() and rely
                    // on the delayed refresh to mark the task done — but that
                    // only ever added the dimming CSS class to the task-item,
                    // it never reset THIS button's text/disabled state (still
                    // stuck showing "Verifying..." from a few lines up). So
                    // the button looked permanently stuck until a full app
                    // reload. Now updated immediately, no refresh needed.
                    if (verifyBtn) {
                        verifyBtn.disabled  = true;
                        verifyBtn.innerText = '✅ Completed';
                        verifyBtn.style.background = '#2ecc71';
                    }
                    const taskItem = verifyBtn?.closest('.task-item');
                    if (taskItem) taskItem.classList.add('done');
                    fetchLiveData();
                } else {
                    showToast(data.message, "error");
                    if (verifyBtn) { verifyBtn.disabled = false; verifyBtn.innerText = "Verify"; }
                }
            } catch (e) {
                showToast("⚠️ Error! Please retry.", "error");
                if (verifyBtn) { verifyBtn.disabled = false; verifyBtn.innerText = "Verify"; }
            } finally {
                _pendingRequests.delete(reqKey);
            }
        }
    );
}

function applyCompletedTasks(completedList) {
    document.querySelectorAll('.task-item').forEach(el => el.classList.remove('done'));
    completedList.forEach(taskId => {
        const item = document.querySelector(`[data-task="${taskId}"]`);
        if (item) item.classList.add('done');
    });
}

// ============================================================
// CHANNEL BUTTONS
// ============================================================
function updateChannelButtons(channelClaims) {
    ['official', 'channel2', 'channel3'].forEach(ch => {
        const btn = document.getElementById(`ch-btn-${ch}`);
        if (!btn) return;
        if (channelClaims[ch]) {
            btn.disabled = true; btn.innerText = "✅ Joined"; btn.style.background = "#2ecc71";
        }
    });

    ['slot1', 'slot2'].forEach(slotId => {
        const btn = document.getElementById(`ch-btn-${slotId}`);
        if (!btn || !CONFIG.SPONSORS?.[slotId]?.active) return;
        const claim       = channelClaims[slotId];
        const currentLink = CONFIG.SPONSORS[slotId].link || '';
        let alreadyClaimed = false;
        if (claim) {
            if (typeof claim === 'object' && claim.claimed_link)
                alreadyClaimed = (claim.claimed_link === currentLink && currentLink !== '');
            else if (claim === true)
                alreadyClaimed = true;
        }
        if (alreadyClaimed) {
            btn.disabled = true; btn.innerText = "✅ Joined"; btn.style.background = "#2ecc71"; btn.onclick = null;
        }
    });
}

function trackSponsorClick(slotId, linkUrl) {
    if (!userId || !linkUrl) return;
    fetch(`${CONFIG.API_BASE_URL}/click_sponsor`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ user_id: userId, slot_id: slotId, link_url: linkUrl })
    }).catch(() => {});
}

// ============================================================
// CHANNEL CLAIM — 15s countdown
// ============================================================
async function claimChannel(channelId, channelUrl) {
    if (!userId) return showToast("User ID not found!", "error");
    const reqKey = `channel_${channelId}`;
    if (_pendingRequests.has(reqKey)) return;

    if (['slot1', 'slot2', 'slot3', 'slot4'].includes(channelId)) trackSponsorClick(channelId, channelUrl);

    if (!openExternalLink(channelUrl)) return;
    _pendingRequests.add(reqKey);

    const btn = document.getElementById(`ch-btn-${channelId}`);
    if (btn) btn.disabled = true;

    startCountdown(15,
        (s) => { if (btn) btn.innerText = `Join & wait ${s}s...`; },
        async () => {
            if (btn) { btn.disabled = true; btn.innerText = '📺 Watch Ad...'; }
            try {
                await requireAdWatch();
            } catch (e) {
                showToast('📺 Watch the full ad to claim your reward!', 'error');
                if (btn) { btn.disabled = false; btn.innerText = '🔄 Retry'; btn.onclick = () => claimChannel(channelId, channelUrl); }
                _pendingRequests.delete(reqKey);
                return;
            }
            if (btn) btn.innerText = 'Claiming...';
            try {
                const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/claim_channel`, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ user_id: userId, channel_id: channelId, channel_url: channelUrl, claimed_link: channelUrl })
                });
                const data = await res.json();
                if (data.status === "success") {
                    showToast(`🎉 ${data.message}`, "success");
                    if (btn) { btn.disabled = true; btn.innerText = "✅ Joined"; btn.style.background = "#2ecc71"; btn.onclick = null; }
                    fetchLiveData();
                } else if (data.status === "not_joined") {
                    showToast("❌ Join not confirmed! Make sure you joined, then tap Retry.", "error");
                    if (btn) {
                        btn.disabled = false; btn.innerText = "🔄 Retry"; btn.style.background = "#e74c3c";
                        btn.onclick = () => {
                            btn.style.background = ''; btn.innerText = "Join & Claim";
                            btn.onclick = () => claimChannel(channelId, channelUrl);
                            claimChannel(channelId, channelUrl);
                        };
                    }
                } else {
                    showToast(data.message, "error");
                    if (btn) { btn.disabled = false; btn.innerText = "Join & Claim"; btn.style.background = ''; btn.onclick = () => claimChannel(channelId, channelUrl); }
                }
            } catch (e) {
                showToast("⚠️ Connection error! Please retry.", "error");
                if (btn) { btn.disabled = false; btn.innerText = "🔄 Retry"; btn.onclick = () => claimChannel(channelId, channelUrl); }
            } finally {
                _pendingRequests.delete(reqKey);
            }
        }
    );
}

// ============================================================
// AD COUNTER
// ============================================================
function updateAdCounter(adsToday, adsDate) {
    const today = new Date().toISOString().split('T')[0];
    const done  = (adsDate === today) ? Math.min(adsToday, MAX_ADS_PER_DAY) : 0;

    const counterEl = document.getElementById('ad-counter');
    const maxEl     = document.getElementById('ad-max');
    if (counterEl) counterEl.innerText = done;
    if (maxEl)     maxEl.innerText     = MAX_ADS_PER_DAY;

    const container = document.getElementById('adsgram-container')
                   || document.getElementById('ad-reward-container')
                   || document.getElementById('ad-container');

    if (done >= MAX_ADS_PER_DAY && container) {
        container.innerHTML = `
            <div style="text-align:center;padding:12px 0;color:#64748b;font-size:13px;">
                ✅ All ${MAX_ADS_PER_DAY} ads watched today! Come back tomorrow.
            </div>`;
    }
}

// ============================================================
// ALL-TASKS COMPLETE BONUS
// ============================================================
function updateAllBonusUI(data) {
    if (!data) return;
    const today = new Date().toISOString().slice(0, 10);

    const lastClaimDt = parseUTCTimestamp(data.last_claim || '');
    const dailyDone   = lastClaimDt ? (lastClaimDt.toISOString().slice(0, 10) === today) : false;

    const adsDate  = data.ads_date || '';
    const adsToday = (adsDate === today) ? (data.ads_today || 0) : 0;
    const adsFull  = adsToday >= MAX_ADS_PER_DAY;

    const completed = data.completed_tasks || [];
    const ytDone    = ['yt1','yt2','yt3'].filter(t => completed.includes(t)).length;
    const webDone   = ['web1','web2','web3'].filter(t => completed.includes(t)).length;

    const alreadyClaimed = (data.allcomplete_bonus_date || '') === today;

    const setCheck = (id, done) => {
        const el = document.getElementById(id);
        if (el) el.textContent = done ? '✅' : '⬜';
    };
    setCheck('check-daily', dailyDone);
    setCheck('check-ads',   adsFull);
    setCheck('check-yt',    ytDone  >= MAX_YT_PER_DAY);
    setCheck('check-web',   webDone >= MAX_WEB_PER_DAY);

    const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
    setText('allbonus-ads-count', `(${Math.min(adsToday, MAX_ADS_PER_DAY)}/${MAX_ADS_PER_DAY})`);
    setText('allbonus-yt-count',  `(${ytDone}/${MAX_YT_PER_DAY})`);
    setText('allbonus-web-count', `(${webDone}/${MAX_WEB_PER_DAY})`);

    const doneCount = [dailyDone, adsFull, ytDone >= MAX_YT_PER_DAY, webDone >= MAX_WEB_PER_DAY].filter(Boolean).length;
    const badge = document.getElementById('allbonus-status-badge');
    if (badge) badge.textContent = `${doneCount}/4`;

    const allDone = dailyDone && adsFull && ytDone >= MAX_YT_PER_DAY && webDone >= MAX_WEB_PER_DAY;
    const btn = document.getElementById('allbonus-btn');
    if (!btn) return;

    if (alreadyClaimed) {
        btn.disabled = true; btn.innerText = '✅ Bonus Claimed Today!'; btn.style.background = '#334155';
    } else if (allDone) {
        btn.disabled = false; btn.innerText = '🏅 Claim Bonus 10 Coins'; btn.style.background = 'linear-gradient(135deg,#22c55e,#16a34a)';
    } else {
        btn.disabled = true; btn.innerText = `🏅 Complete All Tasks (${doneCount}/4)`; btn.style.background = '#1e3a1e';
    }
}

async function claimAllBonus() {
    if (!userId) return showToast('User ID not found!', 'error');
    if (_pendingRequests.has('allbonus')) return;
    _pendingRequests.add('allbonus');

    const btn = document.getElementById('allbonus-btn');
    if (btn) { btn.disabled = true; btn.innerText = '📺 Watch Ad...'; }

    try {
        await requireAdWatch();
    } catch (e) {
        showToast('📺 Watch the full ad to claim your bonus!', 'error');
        if (btn) { btn.disabled = false; btn.innerText = '🏅 Claim Bonus 10 Coins'; }
        _pendingRequests.delete('allbonus');
        return;
    }

    if (btn) btn.innerText = 'Claiming...';
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/claim_allcomplete_bonus/${userId}`, { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
            showToast(data.message || `🎉 ${ALL_TASKS_BONUS} bonus coins credited!`, 'success');
            fetchLiveData();
        } else {
            showToast(data.message || 'Complete all tasks first!', 'error');
            if (btn) { btn.disabled = false; btn.innerText = '🏅 Claim Bonus 10 Coins'; }
        }
    } catch (e) {
        showToast('⚠️ Network error. Please retry.', 'error');
        if (btn) { btn.disabled = false; btn.innerText = '🏅 Claim Bonus 10 Coins'; }
    } finally {
        _pendingRequests.delete('allbonus');
    }
}

// ============================================================
// MANDATORY AD GATE
// ============================================================
async function requireAdWatch() {
    if (!CONFIG.CLAIM_AD_ENABLED) return;
    const zoneId = getMonetagZoneId();
    if (!zoneId) throw new Error('ad_config_missing');
    try { await loadMonetagSdk(); } catch (e) { throw new Error('ad_sdk_failed'); }
    const showMonetagAd = getMonetagShowFunction();
    if (!showMonetagAd) throw new Error('ad_function_missing');
    const result = await showMonetagAd({ ymid: String(userId), requestVar: 'claim_gate' });
    if (!result?.reward_event_type || result.reward_event_type !== 'valued') throw new Error('ad_skipped');
}

// 10-second cooldown after ad — shows countdown on button
async function _adCooldown(btn, resumeLabel) {
    const SECS = 10;
    for (let i = SECS; i > 0; i--) {
        if (btn) btn.innerText = `⏳ Wait ${i}s...`;
        await new Promise(r => setTimeout(r, 1000));
    }
    if (btn && resumeLabel) btn.innerText = resumeLabel;
}

// ============================================================
// MONETAG REWARDED AD
// ============================================================
async function showAd() {
    if (!userId) return showToast("User ID not found!", "error");
    if (!getMonetagZoneId()) return showToast("Monetag Zone ID missing in config.js", "error");
    if (_pendingRequests.has('showAd')) return;
    _pendingRequests.add('showAd');

    const btn = document.querySelector('[onclick="showAd()"]');
    if (btn) { btn.disabled = true; btn.innerText = "Loading Ad..."; }

    try {
        await loadMonetagSdk();
        const showMonetagAd = getMonetagShowFunction();
        if (!showMonetagAd) throw new Error("Monetag ad function unavailable");

        const tokenRes  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/ad_claim_token/${userId}`, { method: 'POST' });
        const tokenData = await tokenRes.json();
        if (tokenData.status !== "success" || !tokenData.token) {
            showToast(tokenData.message || "Ad reward is not available right now.", "error");
            return;
        }

        if (btn) btn.innerText = monetagPreloaded ? "Showing Ad..." : "Preparing Ad...";
        const adResult = await showMonetagAd({ ymid: String(userId), requestVar: 'ad_reward' });
        monetagPreloaded = false;

        if (!adResult?.reward_event_type || adResult.reward_event_type !== 'valued') {
            showToast("Ad was skipped. Watch the full ad to earn coins.", "error");
            preloadMonetagAd();
            return;
        }

        if (btn) btn.innerText = "Crediting...";
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/claim_ad/${userId}`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ token: tokenData.token })
        });
        const data     = await res.json();
        const adsDone  = data.data?.ads_done ?? data.ads_done;

        if (data.status === "success") {
            showToast(`✅ ${data.message}`, "success");
            const counterEl = document.getElementById('ad-counter');
            if (counterEl && adsDone !== undefined) counterEl.innerText = adsDone;
            fetchLiveData();
        } else {
            showToast(data.message || "Unable to claim ad reward.", "error");
        }
    } catch (e) {
        showToast("Ad not completed. No coins awarded.", "error");
    } finally {
        _pendingRequests.delete('showAd');
        if (btn) { btn.disabled = false; btn.innerText = "📺 Watch Ad & Earn 5 Coins"; }
        preloadMonetagAd();
    }
}

// ============================================================
// DEVICE FINGERPRINT & CHECK
// ============================================================
let _fpPromise = null;
function loadFingerprintJS() {
    if (_fpPromise) return _fpPromise;
    _fpPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'https://openfpcdn.io/fingerprintjs/v4/iife.min.js';
        s.async   = true;
        s.onload  = () => resolve(window.FingerprintJS);
        s.onerror = () => reject(new Error('FingerprintJS failed to load'));
        document.head.appendChild(s);
    }).catch(err => { _fpPromise = null; throw err; });
    return _fpPromise;
}

async function generateFingerprint() {
    try {
        const FP     = await loadFingerprintJS();
        const fp     = await FP.load();
        const result = await fp.get();
        if (result?.visitorId) return result.visitorId;
        throw new Error('no visitorId');
    } catch (e) {
        try {
            const data = [
                navigator.userAgent, navigator.language,
                screen.width + "x" + screen.height, screen.colorDepth,
                new Date().getTimezoneOffset(),
                navigator.hardwareConcurrency || "",
                navigator.platform || "",
                navigator.deviceMemory || "",
                (navigator.plugins ? navigator.plugins.length : 0)
            ].join("|");
            const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(data));
            return "wk_" + Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
        } catch (_) { return ""; }
    }
}

async function checkDevice() {
    if (!userId) return;
    try {
        const fingerprint = await generateFingerprint();
        if (!fingerprint) return;
        const res  = await fetch(`${CONFIG.API_BASE_URL}/check_device`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ user_id: userId, fingerprint })
        });
        const data = await res.json();
        // BUG FIX #1: Use _blockVotes — same double-confirmation guard as fetchLiveData()
        if (data.status === "blocked") {
            _blockVotes++;
            if (_blockVotes >= 2) showBlockedView();
        }
    } catch (e) { /* silent — checkDevice failure should never block the UI */ }
}

// ============================================================
// REFERRAL DASHBOARD
// ============================================================
let _refDashData      = null;
let _refDashLoadedAt  = 0;
const REF_CACHE_MS    = 2 * 60 * 1000; // 2 minutes

async function loadReferralDashboard(forceRefresh = false) {
    if (!userId) return;

    // Serve from cache if fresh enough and not forced
    const now = Date.now();
    if (!forceRefresh && _refDashData && (now - _refDashLoadedAt) < REF_CACHE_MS) {
        _renderRefDashboard(_refDashData);
        return;
    }

    // Show subtle loading dots only on first ever load (tiles still show "—")
    ['ref-stat-total','ref-stat-active','ref-stat-today','ref-stat-lifetime'].forEach(id => {
        const el = document.getElementById(id);
        if (el && el.textContent === '—') el.textContent = '…';
    });

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/referral_dashboard/${userId}`);
        const json = await res.json();
        if (json.status === 'success') {
            _refDashData     = json.data;
            _refDashLoadedAt = Date.now();
            _renderRefDashboard(json.data);
            loadCommissionHistory();
        } else {
            _renderRefDashboardFallback();
        }
    } catch(e) {
        _renderRefDashboardFallback();
    }
}

function _renderRefDashboardFallback() {
    // Use existing userData when full dashboard API is unavailable
    const total = (userData.referral_count || getRefCount(userData.referrals || ''));
    const link  = `https://t.me/${CONFIG.BOT_USERNAME}?start=${userId}`;
    const fallback = {
        total_referrals:       total,
        active_referrals:      0,
        today_commission:      0,
        lifetime_commission:   0,
        daily_limit:           200,
        daily_limit_remaining: 200,
        commission_rate_pct:   10,
        active_min_coins:      10,
        referral_link:         link,
        milestones:            [
            {id:'ms_5',  count:5,  reward:500,  badge:null, claimed:false, claimable:false},
            {id:'ms_10', count:10, reward:1000, badge:null, claimed:false, claimable:false},
            {id:'ms_25', count:25, reward:2500, badge:null, claimed:false, claimable:false},
            {id:'ms_50', count:50, reward:0,    badge:'vip',claimed:false, claimable:false},
        ],
        next_milestone: {id:'ms_5', count:5, reward:500, badge:null, claimable:false, progress: Math.min(total,5)},
        recent_referrals: [],
    };
    _renderRefDashboard(fallback);
}

function _renderRefDashboard(d) {
    const $ = id => document.getElementById(id);

    // Stats tiles
    _setText('ref-stat-total',    d.total_referrals);
    _setText('ref-stat-active',   d.active_referrals);
    _setText('ref-stat-today',    d.today_commission + ' 🪙');
    _setText('ref-stat-lifetime', d.lifetime_commission + ' 🪙');

    // Daily limit bar
    const dailyPct = d.daily_limit > 0 ? Math.min(100, Math.round(d.today_commission / d.daily_limit * 100)) : 0;
    _setText('ref-daily-text', `${d.today_commission} / ${d.daily_limit}`);
    const dailyBar = $('ref-daily-bar');
    if (dailyBar) dailyBar.style.width = dailyPct + '%';
    const dailyBadge = $('ref-daily-badge');
    if (dailyBadge) {
        dailyBadge.textContent = `${d.daily_limit_remaining} coins left`;
        dailyBadge.style.color = d.daily_limit_remaining > 50 ? '#4ade80' : '#f87171';
        dailyBadge.style.background = d.daily_limit_remaining > 50 ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)';
    }

    // Milestone progress bar
    const nm = d.next_milestone;
    if (nm) {
        const msPct = nm.count > 0 ? Math.min(100, Math.round(nm.progress / nm.count * 100)) : 100;
        _setText('ref-ms-label', nm.claimable ? '🎯 Milestone Ready!' : '🎯 Next Milestone');
        _setText('ref-ms-text', `${nm.progress} / ${nm.count}`);
        const msBar = $('ref-ms-bar');
        if (msBar) {
            msBar.style.width = msPct + '%';
            msBar.style.background = nm.claimable ? 'linear-gradient(90deg,#4ade80,#22c55e)' : 'linear-gradient(90deg,#f1c40f,#e67e22)';
        }
        const rewardText = nm.reward > 0 ? `Next Reward: ${nm.reward} Coins 🪙` : 'Next Reward: 🏆 VIP Badge';
        _setText('ref-ms-reward-text', rewardText);
    } else {
        const msBlock = $('ref-milestone-block');
        if (msBlock) msBlock.innerHTML = '<p style="font-size:12px;color:#4ade80;text-align:center;font-weight:700;">🏆 All Milestones Completed!</p>';
    }

    // Referral link
    const linkEl = $('display-link');
    if (linkEl) linkEl.textContent = d.referral_link || '';

    // Active count label
    const acLabel = $('ref-active-count-label');
    if (acLabel) acLabel.textContent = `${d.active_referrals} active`;

    // Milestones list
    _renderMilestones(d.milestones, d.active_referrals);

    // Recent referrals
    _renderRecentReferrals(d.recent_referrals);
}

function _setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

function _renderMilestones(milestones, activeRefs) {
    const list = document.getElementById('ref-milestones-list');
    if (!list) return;
    const sourceIcons = { task: '📝', game: '🎮', ad: '📺' };

    list.innerHTML = milestones.map(ms => {
        const pct     = ms.count > 0 ? Math.min(100, Math.round(activeRefs / ms.count * 100)) : 100;
        const label   = ms.reward > 0 ? `+${ms.reward} Coins 🪙` : '🏆 VIP Badge';
        const reached = activeRefs >= ms.count;

        let btnHtml = '';
        if (ms.claimed) {
            btnHtml = `<span style="font-size:11px;color:#4ade80;font-weight:700;">✅ Claimed</span>`;
        } else if (ms.claimable) {
            btnHtml = `<button onclick="claimMilestone('${ms.id}')" style="font-size:11px;background:linear-gradient(90deg,#f1c40f,#e67e22);color:#000;border:none;padding:5px 12px;border-radius:20px;cursor:pointer;font-weight:800;">Claim!</button>`;
        } else {
            btnHtml = `<span style="font-size:11px;color:var(--text-dim);">${ms.count - activeRefs} more</span>`;
        }

        const barColor = ms.claimed ? '#4ade80' : (reached ? '#f1c40f' : '#475569');
        return `
        <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:12px;margin-bottom:8px;${ms.claimed ? 'opacity:0.65;' : ''}">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <div>
                    <span style="font-size:13px;font-weight:700;color:var(--text-primary);">${ms.count} Active Referrals</span>
                    <span style="font-size:11px;color:#f1c40f;font-weight:700;margin-left:8px;">${label}</span>
                </div>
                ${btnHtml}
            </div>
            <div style="background:rgba(255,255,255,0.06);border-radius:20px;height:6px;overflow:hidden;">
                <div style="height:100%;border-radius:20px;background:${barColor};width:${pct}%;transition:width 0.5s;"></div>
            </div>
            <p style="font-size:10px;color:var(--text-dim);margin:4px 0 0 0;">${activeRefs} / ${ms.count} active referrals</p>
        </div>`;
    }).join('');
}

function _renderRecentReferrals(refs) {
    const list = document.getElementById('refer-list');
    if (!list) return;
    if (!refs || refs.length === 0) {
        list.innerHTML = "<p style='color:#94a3b8;text-align:center;font-size:13px;'>No referrals yet. Invite your friends! 🚀</p>";
        return;
    }
    list.innerHTML = refs.map((r, i) => {
        const name   = r.username ? `@${r.username}` : `Friend ${i + 1}`;
        const status = r.active
            ? `<span style="font-size:10px;background:rgba(74,222,128,0.12);color:#4ade80;padding:2px 7px;border-radius:20px;font-weight:700;">✅ Active</span>`
            : `<span style="font-size:10px;background:rgba(255,255,255,0.05);color:#64748b;padding:2px 7px;border-radius:20px;">Inactive</span>`;
        return `
        <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
            <span style="font-size:18px;">${r.active ? '🟢' : '⚪'}</span>
            <div style="flex:1;">
                <p style="margin:0;font-size:13px;font-weight:600;color:var(--text-primary);">${name}</p>
                <p style="margin:0;font-size:10px;color:var(--text-dim);">Coins: ${r.coins || 0} 🪙 • Joined: ${r.joined || '—'}</p>
            </div>
            ${status}
        </div>`;
    }).join('');
}

async function loadCommissionHistory() {
    const list = document.getElementById('commission-history-list');
    if (!list || !userId) return;
    list.innerHTML = "<p style='color:#94a3b8;text-align:center;font-size:12px;'>Loading...</p>";
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/referral_commission_history/${userId}`);
        const json = await res.json();
        if (json.status !== 'success') { list.innerHTML = "<p style='color:#94a3b8;text-align:center;font-size:12px;'>No history yet.</p>"; return; }
        const data = json.data || [];
        if (data.length === 0) {
            list.innerHTML = "<p style='color:#94a3b8;text-align:center;font-size:12px;'>No commissions earned yet. Invite friends! 🚀</p>";
            return;
        }
        const srcIcon = { task: '📝', game: '🎮', ad: '📺' };
        list.innerHTML = data.map(h => `
            <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
                <span style="font-size:18px;">${srcIcon[h.source] || '💰'}</span>
                <div style="flex:1;">
                    <p style="margin:0;font-size:12px;font-weight:600;color:var(--text-primary);">+${h.commission} coins <span style="font-size:10px;color:#a78bfa;">(from ${h.earner_name || 'Unknown'})</span></p>
                    <p style="margin:0;font-size:10px;color:var(--text-dim);">${h.source || 'earning'} • ${h.timestamp}</p>
                </div>
                <span style="font-size:11px;color:var(--text-dim);">${h.coins_earned}🪙 × 10%</span>
            </div>`).join('');
    } catch(e) {
        list.innerHTML = "<p style='color:#94a3b8;text-align:center;font-size:12px;'>Failed to load. Try again.</p>";
    }
}

async function claimMilestone(milestoneId) {
    if (!userId) return showToast("User ID not found!", "error");
    const key = `claim_ms_${milestoneId}`;
    if (_pendingRequests.has(key)) return;
    _pendingRequests.add(key);
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/claim_milestone/${userId}`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ milestone_id: milestoneId }),
        });
        const json = await res.json();
        if (json.status === 'success') {
            showToast(json.message || 'Milestone claimed!', 'success');
            setTimeout(() => { loadReferralDashboard(); fetchLiveData(); }, 600);
        } else {
            showToast(json.message || 'Claim failed.', 'error');
        }
    } catch(e) {
        showToast('Server error. Try again.', 'error');
    } finally {
        _pendingRequests.delete(key);
    }
}

function copyReferralLink() {
    const linkEl = document.getElementById('display-link');
    const link   = (linkEl && linkEl.textContent.trim() !== 'Loading...')
        ? linkEl.textContent.trim()
        : `https://t.me/${CONFIG.BOT_USERNAME}?start=${userId}`;
    copyText(link, 'Referral link copied!');
}

async function copyText(value, successMessage = 'Copied!') {
    const text = String(value || '');
    if (!text) return showToast('Nothing to copy.', 'error');

    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
        } else {
            const helper = document.createElement('textarea');
            helper.value = text;
            helper.setAttribute('readonly', '');
            helper.style.position = 'fixed';
            helper.style.opacity = '0';
            document.body.appendChild(helper);
            helper.select();
            document.execCommand('copy');
            helper.remove();
        }
        showToast(`✅ ${successMessage}`, 'success');
    } catch (_) {
        showToast('Copy failed. Please copy it manually.', 'error');
    }
}

function updateReferralList(referrals) {
    // Legacy shim — actual rendering now done by loadReferralDashboard
    if (_refDashData) return;
    const list = document.getElementById('refer-list');
    if (!list) return;
    const refCount = getRefCount(referrals);
    if (refCount === 0) {
        list.innerHTML = "<p style='color:#94a3b8;text-align:center;font-size:13px;'>No referrals yet. Invite your friends! 🚀</p>";
        return;
    }
    const refs = referrals.split(',').filter(id => id.trim() !== '');
    list.innerHTML = refs.map((id, i) => `
        <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #1e293b;">
            <span style="font-size:16px;">👤</span>
            <div>
                <p style="margin:0;font-size:13px;font-weight:600;color:#e2e8f0;">Friend ${i + 1}</p>
                <p style="margin:0;font-size:11px;color:#94a3b8;">ID: ${id.trim()}</p>
            </div>
            <span style="margin-left:auto;font-size:12px;color:#2ecc71;font-weight:700;">+30 🪙</span>
        </div>`).join('');
}


// ============================================================
// ₹ RUPEE WALLET — Withdraw & History
// ============================================================
let _selectedRupeeMethod = 'upi';

function selectRupeeMethod(method) {
    _selectedRupeeMethod = method;
    ['upi', 'google'].forEach(m => {
        const btn   = document.getElementById(`rupee-method-btn-${m}`);
        const panel = document.getElementById(`rupee-input-${m}`);
        const active = m === method;
        if (btn) {
            btn.style.borderColor = active ? '#4ade80' : 'rgba(255,255,255,0.10)';
            btn.style.color       = active ? '#4ade80' : '#94a3b8';
            btn.style.background  = active ? 'rgba(34,197,94,0.15)' : 'rgba(255,255,255,0.05)';
        }
        if (panel) panel.style.display = active ? '' : 'none';
    });
}

async function requestRupeeWithdraw() {
    if (!userId) return showToast("User ID not found!", "error");
    if (_pendingRequests.has('rupeeWithdraw')) return showToast("Request in progress...", "error");

    const amountEl = document.getElementById('rupee-withdraw-amount');
    const rawAmt   = amountEl ? amountEl.value.trim() : '';
    const amount   = parseFloat(rawAmt);
    const method   = _selectedRupeeMethod;

    if (!rawAmt || isNaN(amount)) return showToast("Please enter a valid amount!", "error");
    if (amount < 30)              return showToast("Minimum withdrawal is ₹30.", "error");

    // ── 5 Referral Check for Rupee Withdrawal ─────────────────────────────
    if (CONFIG.REFERRAL_ACTIVE === true) {
        const refCount  = getRefCount(userData.referrals);
        const isPrem    = !!(userData && userData.premium_info && userData.premium_info.premium);
        const refNeeded = isPrem ? 2 : 5;
        updateRupeeRefUI(refCount, refNeeded);
        if (refCount < refNeeded) {
            return showToast(`${refNeeded - refCount} aur referral chahiye rupee withdrawal ke liye! 👥`, "error");
        }
    }

    let paymentAddress = '';
    if (method === 'upi') {
        const upi = document.getElementById('rupee-upi-id')?.value.trim();
        if (!upi || !upi.includes('@')) return showToast("Please enter a valid UPI ID! (name@upi)", "error");
        paymentAddress = upi;
    } else {
        paymentAddress = 'via_telegram';
    }

    _pendingRequests.add('rupeeWithdraw');
    const btn = document.querySelector('[onclick="requestRupeeWithdraw()"]');
    if (btn) { btn.disabled = true; btn.innerText = "Processing..."; }

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/withdraw_rupees`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ user_id: userId, method, payment_address: paymentAddress, amount }),
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast(`💰 ${data.message}`, "success");
            if (amountEl) amountEl.value = '';
            const upiEl = document.getElementById('rupee-upi-id');
            if (upiEl) upiEl.value = '';
            fetchLiveData();
            loadRupeeHistory();
        } else {
            showToast(data.message || "An error occurred.", "error");
        }
    } catch (e) {
        showToast("⚠️ Connection error! Please retry.", "error");
    } finally {
        _pendingRequests.delete('rupeeWithdraw');
        if (btn) { btn.disabled = false; btn.innerText = "Withdraw Rupees"; }
    }
}

// ── RUPEE REFERRAL PROGRESS UI ──────────────────────────────────────────────
// Reuses the same referral-check logic as the coins withdrawal.
// When REFERRAL_ACTIVE is false the requirement is bypassed automatically.
function updateRupeeRefUI(refCount, refNeeded) {
    const text  = document.getElementById('rupee-ref-progress-text');
    const bar   = document.getElementById('rupee-ref-progress-bar');
    const box   = document.getElementById('rupee-ref-requirement-box');
    const hint  = document.getElementById('rupee-ref-hint');
    const wdBtn = document.getElementById('rupee-withdraw-btn');
    if (!text || !bar) return;

    const bypass = CONFIG.REFERRAL_ACTIVE === false;
    const met    = bypass || refCount >= refNeeded;

    // Progress text & bar
    text.innerText   = bypass ? '✅ Not Required' : `${refCount} / ${refNeeded}${met ? ' ✅' : ''}`;
    text.style.color = met ? '#22c55e' : '#ef4444';
    bar.style.width  = bypass ? '100%' : Math.min((refCount / refNeeded) * 100, 100) + '%';
    bar.style.background = met
        ? 'linear-gradient(90deg,#22c55e,#16a34a)'
        : 'linear-gradient(90deg,#ef4444,#b91c1c)';

    // Card border colour
    if (box) box.style.borderColor = met ? 'rgba(34,197,94,0.4)' : 'rgba(239,68,68,0.4)';

    // Hint message — shown only when requirement is not met
    if (hint) hint.style.display = met ? 'none' : '';

    // Enable / disable the Withdraw button
    if (wdBtn) {
        wdBtn.disabled        = !met;
        wdBtn.style.opacity   = met ? '1'           : '0.45';
        wdBtn.style.cursor    = met ? 'pointer'     : 'not-allowed';
    }
}

async function loadRupeeHistory() {
    const list = document.getElementById('rupee-history-list');
    if (!list || !userId) return;
    list.innerHTML = "<p class='spinner'>Loading...</p>";
    try {
        const res     = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_rupee_history/${userId}`);
        const data    = await res.json();
        const history = data.data?.history || [];
        if (history.length > 0) {
            const icons = { upi: '🏦', google_redeem: '🎁' };
            const names = { upi: 'UPI', google_redeem: 'Google Play' };
            list.innerHTML = history.map(h => {
                const color = h.status.includes('Approved') ? '#22c55e'
                            : h.status.includes('Rejected') ? '#e74c3c' : '#f1c40f';
                const m    = h.method || 'upi';
                const addr = h.payment_address === 'via_telegram' ? 'via Telegram DM' : (h.payment_address || '—');
                return `
                <div class="history-item">
                    <div>${icons[m]||'💰'} <b>₹${parseFloat(h.amount||0).toFixed(2)}</b> — ${names[m]||'UPI'}: <span style="color:#94a3b8;font-size:12px;">${addr}</span></div>
                    <div class="history-status" style="color:${color}">${h.status} • ${h.date}</div>
                </div>`;
            }).join('');
        } else {
            list.innerHTML = "<p style='color:#94a3b8;text-align:center;font-size:13px;'>No rupee withdrawals yet.</p>";
        }
    } catch (e) {
        list.innerHTML = "<p style='color:#94a3b8;text-align:center;'>Failed to load history.</p>";
    }
}

// ============================================================
// WITHDRAWAL HISTORY
// ============================================================
// ============================================================
// SUPPORT
// ============================================================
async function sendSupport() {
    if (_pendingRequests.has('support')) return;
    const msgEl = document.getElementById('support-msg');
    const msg   = msgEl ? msgEl.value.trim() : '';
    if (!msg)              return showToast("Please write a message!", "error");
    if (!userId)           return showToast("User ID not found!", "error");
    if (msg.length > 1000) return showToast("Message too long! Maximum 1000 characters.", "error");

    _pendingRequests.add('support');
    const btn = document.querySelector('[onclick="sendSupport()"]');
    if (btn) { btn.disabled = true; btn.innerText = "Sending..."; }

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/send_support`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ user_id: userId, message: msg })
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast("✅ Your message has been sent to Admin!", "success");
            if (msgEl) msgEl.value = '';
        } else {
            showToast(data.message || "Failed to send message.", "error");
        }
    } catch (e) {
        showToast("⚠️ Could not send message. Check your connection.", "error");
    } finally {
        _pendingRequests.delete('support');
        if (btn) { btn.disabled = false; btn.innerText = "Send to Admin"; }
    }
}

// ============================================================
// UTILITY
// ============================================================
function showBlockedView() {
    // BUG FIX: this used to unconditionally wipe every tab and force Help/Support
    // back open on EVERY call — and fetchLiveData() re-calls this every 5 minutes
    // (plus after most actions). So a blocked user who navigated to Profile got
    // yanked back to Support automatically within minutes. Now: only force a tab
    // open the first time (nothing allowed is showing yet); once help or profile
    // is already active, leave it exactly as the user left it.
    const currentlyOnAllowedTab =
        document.getElementById('help')?.classList.contains('active-tab') ||
        document.getElementById('profile')?.classList.contains('active-tab');

    if (!currentlyOnAllowedTab) {
        document.querySelectorAll('.tab-content').forEach(el => { el.style.display = 'none'; el.classList.remove('active-tab'); });
        const helpTab = document.getElementById('help');
        if (helpTab) { helpTab.style.display = 'block'; helpTab.classList.add('active-tab'); }
        const titleEl = document.getElementById('tab-title');
        if (titleEl) titleEl.textContent = '🚫 Account Blocked';
    }

    // Bottom-nav: keep visible but only let them reach Support (default) and
    // Profile — Home/Tasks/Top/Refer stay disabled since those involve
    // earning/spending coins, which stays locked while banned.
    const nav = document.querySelector('.bottom-nav');
    if (nav) nav.style.display = 'flex';
    ['nav-home', 'nav-tasks', 'nav-leaderboard', 'nav-refer'].forEach(id => {
        const item = document.getElementById(id);
        if (item) {
            item.style.opacity = '0.35';
            item.style.pointerEvents = 'none';
            item.onclick = null;
        }
    });
    const profileNav = document.getElementById('nav-profile');
    if (profileNav) profileNav.style.opacity = '1';

    const banner = document.getElementById('blocked-banner');
    if (banner) banner.style.display = 'block';
}

function copyEmail() {
    copyText('cdoternsupport@gmail.com', 'Email copied!');
    const status = document.getElementById('copy-status');
    if (status) { status.style.display = 'block'; setTimeout(() => { status.style.display = 'none'; }, 2000); }
}

async function inviteFriend() {
    if (!userId) return showToast("User ID not found!", "error");
    if (_pendingRequests.has('inviteFriend')) return;
    _pendingRequests.add('inviteFriend');

    const link    = `https://t.me/${CONFIG.BOT_USERNAME}?start=${userId}`;
    const btn     = document.querySelector('[onclick="inviteFriend()"]');
    if (btn) { btn.disabled = true; btn.innerText = "Opening..."; }

    try {
        const shareText = '💰 Earn coins daily by watching ads & completing tasks! 🚀 Join now and start earning instantly!';
        if (tg?.openTelegramLink) {
            tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(shareText)}`);
        } else if (navigator.share) {
            navigator.share({ text: `${shareText}\n${link}` }).catch(() => {});
        } else {
            navigator.clipboard.writeText(link).catch(() => {});
            showToast("✅ Invite link copied!", "success");
        }
    } catch (e) {
        navigator.clipboard.writeText(link).catch(() => {});
        showToast("✅ Invite link copied!", "success");
    } finally {
        _pendingRequests.delete('inviteFriend');
        if (btn) { btn.disabled = false; btn.innerText = "Invite Friends"; }
    }
}

function openAdminTelegram() {
    const u = String(CONFIG.ADMIN_TELEGRAM || '');
    if (!u) return showToast("Admin contact not configured.", "error");
    const username = u.startsWith('@') ? u.slice(1) : u;
    if (tg?.openTelegramLink) tg.openTelegramLink(`https://t.me/${username}`);
    else window.open(`https://t.me/${username}`, '_blank');
}

// ============================================================
// TAB SWITCHER
// ============================================================
function switchTab(tabId, el) {
    // ── Tournament Lock Mode intercept ────────────────────────────────────
    // Leaderboard, Profile, and Tournament Hub are NEVER blocked.
    const _TL_SAFE_TABS = new Set(['leaderboard', 'profile', 'tournament']);
    if (!_TL_SAFE_TABS.has(tabId)) {
        const tlCfg = window._featureCfgCache;
        if (tlCfg && tlCfg.tournament_lock_active) {
            const locked = new Set(tlCfg.tournament_lock_features || []);
            // Map tab IDs to feature keys (bomb-box uses bomb_box key)
            const featureKey = tabId === 'bomb-box' ? 'bomb_box' : tabId;
            if (locked.has(featureKey)) {
                // Still switch to the tab — the overlay already sits on top
                // (applied by applyTournamentLock). This lets users see the
                // lock message with the "View Tournament" button.
            }
        }
    }
    // ─────────────────────────────────────────────────────────────────────

    document.querySelectorAll('.tab-content').forEach(t => { t.style.display = 'none'; t.classList.remove('active-tab'); });
    const tab = document.getElementById(tabId);
    if (tab) { tab.style.display = 'block'; tab.classList.add('active-tab'); }

    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    if (el) el.classList.add('active');
    try { sessionStorage.setItem('activeTab', tabId); } catch(_) {}

    const titleMap = {
        rewards:     'Rewards',
        tasks:       'Daily Tasks',
        leaderboard: 'Top Earners',
        refer:       'Refer & Earn',
        help:        'Help & Support',
        spin:        '🎡 Spin Wheel',
        mining:      '⛏️ Coin Mining',
        tournament:  '🏆 Tournament Hub',
        'bomb-box':  '💣 Bomb Box',
    };
    const titleEl = document.getElementById('tab-title');
    if (titleEl) titleEl.textContent = titleMap[tabId] || '';

    if (tabId === 'leaderboard') {
        // ✅ FIX: Cached data instantly dikhao phir background refresh
        if (userData && userData.leaderboard && userData.leaderboard !== "none") {
            updateLeaderboardUI(userData.leaderboard);
        }
        refreshLeaderboard();
    }
    if (tabId === 'refer')       loadReferralDashboard();

    // Re-apply referral lock on tab switch (for both rewards and refer tabs)
    if (tabId === 'rewards' || tabId === 'refer') setTimeout(applyReferralLock, 50);
}

// ============================================================
// SPONSOR SLOTS
// ============================================================
function renderSponsorSlots(channelClaims, completedTasks, verifyCompletions) {
    const container = document.getElementById('sponsor-slots-container');
    if (!container) return;

    const sponsors = CONFIG.SPONSORS || {};
    const claims   = channelClaims  || {};
    const done     = completedTasks || [];
    let html = '';

    ['slot1', 'slot2', 'slot3', 'slot4'].forEach(slotId => {
        const s = sponsors[slotId];
        if (!s) return;

        const icon   = s.icon   || '💼';
        const name   = s.name   || ('Sponsor ' + slotId);
        const desc   = s.desc   || '';
        const link   = s.link   || '#';
        const reward = s.reward || 5;
        const type   = s.type   || 'channel';
        const active = s.active === true;

        if (!active) {
            html += `
            <div style="position:relative;display:flex;align-items:center;gap:12px;padding:10px;
                        background:rgba(255,255,255,0.04);border-radius:10px;margin-bottom:8px;
                        overflow:hidden;min-height:58px;">
                <div style="font-size:26px;">${icon}</div>
                <div style="flex:1;">
                    <p style="font-size:13px;font-weight:600;color:#475569;margin:0;">${name}</p>
                    <p style="font-size:11px;color:#334155;margin:2px 0 0 0;">Contact admin to activate</p>
                </div>
                <button class="btn-sm" style="background:#38bdf8;color:#000;opacity:0.4;" disabled>Locked</button>
            </div>`;
            return;
        }

        const claim = claims[slotId];
        let alreadyClaimed = false;
        if (claim) {
            if (typeof claim === 'object' && claim.claimed_link)
                alreadyClaimed = (claim.claimed_link === link && link !== '');
            else if (claim === true)
                alreadyClaimed = true;
        }

        if (type === 'verify') {
            const vc = (verifyCompletions || {})[slotId] || {};
            const isVerifyDone = done.includes(slotId) && (!vc.link || vc.link === link);
            const inputId = `${slotId}-code-input`;
            html += `
            <div class="partner-card" style="margin-bottom:8px;">
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
                    <span style="font-size:22px;">${icon}</span>
                    <div style="flex:1;">
                        <p style="font-size:13px;font-weight:700;color:#3498db;margin:0;">${name}</p>
                        <p style="font-size:11px;color:#94a3b8;margin:2px 0 0 0;">${desc}</p>
                    </div>
                    <span style="font-size:12px;color:#f1c40f;font-weight:700;">+${reward} 🪙</span>
                </div>
                ${isVerifyDone
                    ? `<button class="btn-sm" style="width:100%;background:#334155;color:#64748b;" disabled>✅ Completed (One-time)</button>`
                    : `<button class="btn-sm" style="background:#3498db;width:100%;margin-bottom:8px;font-weight:700;"
                            onclick="openExternalLink('${escapeHtml(link)}')">🌐 Visit Site</button>
                        <div style="display:flex;gap:8px;">
                            <input type="text" id="${inputId}" placeholder="Enter code"
                                style="flex:1;padding:8px 10px;background:#1e293b;border:1px solid #334155;
                                       border-radius:8px;color:#e2e8f0;font-size:13px;text-transform:uppercase;"
                                maxlength="20">
                            <button class="btn-sm" data-verify-btn="${slotId}"
                                style="background:linear-gradient(135deg,#3498db,#2980b9);font-weight:700;"
                                onclick="verifyTask('${slotId}', '${inputId}', '${link}')">Verify</button>
                        </div>`
                }
            </div>`;

        } else if (type === 'task') {
            html += `
            <div class="partner-card" style="margin-bottom:8px;">
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
                    <span style="font-size:22px;">${icon}</span>
                    <div style="flex:1;">
                        <p style="font-size:13px;font-weight:700;color:#a855f7;margin:0;">${name}</p>
                        <p style="font-size:11px;color:#94a3b8;margin:2px 0 0 0;">${desc}</p>
                    </div>
                    <span style="font-size:12px;color:#f1c40f;font-weight:700;">+${reward} 🪙</span>
                </div>
                ${alreadyClaimed
                    ? `<button class="btn-sm" style="width:100%;background:#334155;color:#64748b;" disabled>✅ Completed</button>`
                    : `<button class="btn-sm" style="width:100%;background:linear-gradient(135deg,#a855f7,#7c3aed);color:#fff;font-weight:700;"
                            onclick="claimChannel('${slotId}', '${link}')">Open & Claim +${reward} 🪙</button>`
                }
            </div>`;

        } else {
            html += `
            <div style="display:flex;align-items:center;gap:12px;padding:10px;
                        background:rgba(255,255,255,0.05);border-radius:10px;margin-bottom:8px;">
                <div style="font-size:26px;">${icon}</div>
                <div style="flex:1;">
                    <p style="font-size:13px;font-weight:600;color:#e2e8f0;margin:0;">${name}</p>
                    <p style="font-size:11px;color:#94a3b8;margin:2px 0 0 0;">${desc}</p>
                </div>
                ${alreadyClaimed
                    ? `<button class="btn-sm" style="background:#2ecc71;color:#000;" disabled>✅ Joined</button>`
                    : `<button id="ch-btn-${slotId}" class="btn-sm ch-claim-btn"
                            style="background:linear-gradient(135deg,#38bdf8,#0ea5e9);color:#000;font-weight:700;"
                            onclick="claimChannel('${slotId}', '${link}')">+${reward} 🪙 Join</button>`
                }
            </div>`;
        }
    });

    container.innerHTML = html || '<p style="color:#475569;text-align:center;font-size:13px;">No sponsor slots configured.</p>';
}

// ============================================================
// REFERRAL LOCK — Refer tab + Withdraw card
// ============================================================
function applyReferralLock() {
    const withdrawTab = document.getElementById('withdraw-card');
    const referTab    = document.getElementById('refer');
    const refBox      = document.getElementById('ref-requirement-box');
    const refText     = document.getElementById('ref-progress-text');
    const refBarWrap  = document.getElementById('ref-bar-wrap');
    const helpRef     = document.getElementById('help-ref-rule');

    const refCount   = getRefCount(userData.referrals);
    const refsMet    = refCount >= 5;
    const lockActive = CONFIG.REFERRAL_ACTIVE !== false && !refsMet;

    // ── Refer TAB lock — lock tab when REFERRAL_ACTIVE is false ───────────
    if (CONFIG.REFERRAL_ACTIVE === false) {
        if (referTab && !referTab.querySelector('.refer-tab-lock-overlay')) {
            const ov = document.createElement('div');
            ov.className = 'refer-tab-lock-overlay';
            ov.style.cssText = [
                'position:absolute', 'inset:0', 'display:flex', 'flex-direction:column',
                'align-items:center', 'justify-content:center',
                'background:rgba(10,15,30,0.90)', 'backdrop-filter:blur(6px)',
                'z-index:9999', 'pointer-events:all', 'cursor:default',
            ].join(';');
            ov.innerHTML =
                '<span style="font-size:52px;animation:lock-pulse 1.8s ease-in-out infinite;display:block;">🔒</span>' +
                '<span style="font-size:16px;color:#f1c40f;font-weight:800;margin-top:14px;letter-spacing:0.5px;">Referral Coming Soon!</span>' +
                '<span style="font-size:13px;color:#94a3b8;margin-top:6px;">Stay tuned for updates</span>';
            ov.addEventListener('click', e => e.stopPropagation());
            referTab.appendChild(ov);
        }
    } else {
        if (referTab) {
            const stale = referTab.querySelector('.refer-tab-lock-overlay');
            if (stale) stale.remove();
        }
    }

    // ── CASE 1: Referrals not yet completed ───────────────────────────────────
    if (lockActive) {
        // BUG FIX #6: this used to call _removeWithdrawLock here — the exact
        // opposite of what CASE 1 means (refs NOT met yet). No function ever
        // applied the withdraw lock, so the withdraw card never visually
        // showed as locked even when referrals were incomplete. (The actual
        // withdrawal request was still safely blocked server-side either way —
        // this fixes the visual/UX bypass, not a money bug.)
        _applyWithdrawLock(withdrawTab, refCount);
        if (refBox)  { refBox.style.borderColor = '#e74c3c'; refBox.style.opacity = '1'; }
        if (refText) { refText.style.color = '#e74c3c'; }
        if (helpRef) helpRef.innerHTML = '• Referral Requirement: <b style="color:#f1c40f;">5 Users</b>';

    // ── CASE 2: REFERRAL_ACTIVE = false — bypass mode ──────────────────────
    } else if (CONFIG.REFERRAL_ACTIVE === false) {
        _removeWithdrawLock(withdrawTab);
        if (refBox)  { refBox.style.borderColor = '#2ecc71'; refBox.style.opacity = '1'; }
        if (refText) { refText.innerText = '✅ Not Required'; refText.style.color = '#2ecc71'; }
        if (refBarWrap) {
            refBarWrap.innerHTML =
                '<div style="height:100%;background:linear-gradient(90deg,#2ecc71,#27ae60);' +
                'border-radius:20px;width:100%;transition:width 0.5s;"></div>';
        }
        if (helpRef) helpRef.innerHTML = '• Referral Requirement: <b style="color:#2ecc71;">Not Required ✅</b>';

    // ── CASE 3: Refs poore hain — lock hata do ─────────────────────────────
    } else {
        _removeWithdrawLock(withdrawTab);
        if (refBox)  { refBox.style.borderColor = '#2ecc71'; refBox.style.opacity = '1'; }
        if (refText) { refText.style.color = '#2ecc71'; }
        if (helpRef) helpRef.innerHTML = '• Referral Requirement: <b style="color:#2ecc71;">Completed ✅</b>';
    }
}

function _removeWithdrawLock(withdrawTab) {
    if (!withdrawTab) withdrawTab = document.getElementById('withdraw-card');
    if (withdrawTab) {
        const stale = withdrawTab.querySelector('.refer-lock-overlay');
        if (stale) stale.remove();
        const btn = withdrawTab.querySelector('[onclick="requestWithdraw()"]');
        if (btn) btn.disabled = false;
    }
}

/** BUG FIX #6: was never defined/called — see CASE 1 above. */
function _applyWithdrawLock(withdrawTab, refCount) {
    if (!withdrawTab) withdrawTab = document.getElementById('withdraw-card');
    if (!withdrawTab) return;
    const btn = withdrawTab.querySelector('[onclick="requestWithdraw()"]');
    if (btn) btn.disabled = true;
    if (withdrawTab.querySelector('.refer-lock-overlay')) return;
    const ov = document.createElement('div');
    ov.className = 'refer-lock-overlay app-lock-pill';
    ov.innerHTML = `<span class="lock-label">Refer ${5 - (refCount || 0)} more to unlock withdrawal</span>`;
    withdrawTab.prepend(ov);
}

// ============================================================
// TOURNAMENT LOCK MODE
// ============================================================
// Admin can enable this anytime via POST /admin/tournament_lock.
// When active, selected features show a lock overlay. Tournament,
// Leaderboard, and Profile are ALWAYS accessible.

const _TL_MSG = '🔒 Tournament is Live. This feature is temporarily unavailable.';

// Last known feature config — set by loadSpinStatus() on every refresh.
// Used by switchTab() without requiring an extra API call.
window._featureCfgCache = null;

/**
 * Apply or remove tournament lock overlays across all configured features.
 * Called automatically each time the feature config is fetched (loadSpinStatus).
 * @param {object} cfg — result from /get_feature_config
 */
function applyTournamentLock(cfg) {
    // Cache for switchTab() to reuse without an extra fetch
    window._featureCfgCache = cfg;

    const lockActive = !!(cfg && cfg.tournament_lock_active);
    const locked     = new Set(lockActive ? (cfg.tournament_lock_features || []) : []);

    // ── TAB containers (rewards / tasks / refer) ─────────────────────────
    const TAB_MAP = {
        rewards: 'rewards',
        tasks:   'tasks',
        refer:   'refer',
    };
    Object.entries(TAB_MAP).forEach(([key, tabId]) => {
        const tabEl = document.getElementById(tabId);
        if (!tabEl) return;
        if (lockActive && locked.has(key)) {
            _tl_applyOverlay(tabEl, 'tl-tab-lock-' + key);
        } else {
            _tl_removeOverlay(tabEl, 'tl-tab-lock-' + key, /* skipButtonToggle */ true);
        }
    });

    // ── Feature cards (spin / mining / bomb_box) ─────────────────────────
    const CARD_MAP = {
        spin:     'spin-card',
        mining:   'mining-card',
        bomb_box: 'bomb-box-card',
    };
    Object.entries(CARD_MAP).forEach(([key, cardId]) => {
        const cardEl = document.getElementById(cardId);
        if (!cardEl) return;
        if (lockActive && locked.has(key)) {
            _tl_applyOverlay(cardEl, 'tl-card-lock-' + key);
        } else {
            _tl_removeOverlay(cardEl, 'tl-card-lock-' + key);
        }
    });

    // ── Lottery card ──────────────────────────────────────────────────────
    const lotteryCard = document.getElementById('lottery-card');
    if (lotteryCard) {
        if (lockActive && locked.has('lottery')) {
            _tl_applyOverlay(lotteryCard, 'tl-card-lock-lottery');
        } else {
            _tl_removeOverlay(lotteryCard, 'tl-card-lock-lottery');
        }
    }

    // ── Nav item visual dimming (🔒 on locked nav items) ─────────────────
    // Map: tabId used in onclick → feature key
    const NAV_FEATURE_MAP = {
        rewards: 'rewards',
        tasks:   'tasks',
        refer:   'refer',
        spin:    'spin',
        mining:  'mining',
        'bomb-box': 'bomb_box',
    };
    document.querySelectorAll('.nav-item').forEach(nav => {
        const onclickAttr = nav.getAttribute('onclick') || '';
        const match = onclickAttr.match(/switchTab\('([^']+)'/);
        if (!match) return;
        const tabId      = match[1];
        const featureKey = NAV_FEATURE_MAP[tabId];
        if (!featureKey) return;
        if (lockActive && locked.has(featureKey)) {
            nav.style.opacity = '0.45';
            // Add tiny lock badge if not already there
            if (!nav.querySelector('.tl-nav-lock')) {
                const badge = document.createElement('span');
                badge.className  = 'tl-nav-lock';
                badge.textContent = '🔒';
                badge.style.cssText =
                    'position:absolute;top:2px;right:2px;font-size:9px;line-height:1;';
                nav.style.position = 'relative';
                nav.appendChild(badge);
            }
        } else {
            nav.style.opacity = '';
            const badge = nav.querySelector('.tl-nav-lock');
            if (badge) badge.remove();
        }
    });
}

/** Inject a tournament-lock overlay onto a container element. */
function _tl_applyOverlay(el, cls) {
    if (!el) return;
    // BUG FIX #5: this overlay only ever added a decorative pill — it never
    // disabled anything underneath, so Tournament Lock Mode showed "Locked"
    // on spin/mining/bomb-box/lottery/tabs while every button inside stayed
    // fully clickable. Now matches _applyFeatureLock: dim + disable buttons.
    el.classList.add('locked-card');
    el.querySelectorAll('button').forEach(b => { b.disabled = true; });

    if (el.querySelector('.' + cls)) return;
    el.style.overflow = 'hidden';

    const ov = document.createElement('div');
    ov.className  = cls + ' tl-overlay app-lock-pill';
    ov.innerHTML = '<p>Tournament Mode Active</p>';
    el.prepend(ov);
}

/** Remove a tournament-lock overlay from an element.
 * @param {boolean} skipButtonToggle — TAB-level containers (rewards/tasks/
 *   refer) hold MANY independently-managed buttons (ad-watch, task-verify,
 *   channel-claim, streak-claim, etc.) that each own their own disabled
 *   state. Blindly re-enabling every button here on every normal refresh
 *   (the common case, since tournament lock usually isn't even active) was
 *   wiping out legitimate states — most visibly, a just-completed task's
 *   button going back to "active" a moment later, and unrelated
 *   already-locked cards (e.g. Bomb Box marked Coming Soon) making the
 *   ENTIRE tab look dimmed/disabled. Card-level calls (spin/mining/
 *   bomb-box/lottery) don't pass this — those each have their own loader
 *   that re-derives every button's correct state right after this runs in
 *   the same call, so syncing there is safe.
 */
function _tl_removeOverlay(el, cls, skipButtonToggle) {
    if (!el) return;
    const ov = el.querySelector('.' + cls);
    const wasLocked = !!ov;
    if (ov) ov.remove();

    if (skipButtonToggle) {
        if (wasLocked && !el.querySelector('.app-lock-pill')) {
            el.classList.remove('locked-card');
        }
        return;
    }
    _syncCardLockVisual(el);
}

// ============================================================
// TOURNAMENT HUB
// ============================================================

// Multi-tournament state
let _allTournaments     = [];          // List of all active tournaments
let _selectedTid        = null;        // Currently selected tournament_id
let _tournamentCache    = {};          // { tid: { tournament, winners, ts } }
let _tournamentRegCache = {};          // { tid: regData }
const TOURNAMENT_CACHE_TTL = 60 * 1000;

function openTournamentHub(e) {
    if (e) e.stopPropagation();
    const modal = document.getElementById('tournament-modal');
    if (!modal) return;
    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
    loadTournamentData();
}

function closeTournamentHub() {
    const modal = document.getElementById('tournament-modal');
    if (!modal) return;
    modal.classList.remove('open');
    document.body.style.overflow = '';
    // Stop the countdown ticking in the background once the hub is closed.
    if (_matchCountdownInterval) { clearInterval(_matchCountdownInterval); _matchCountdownInterval = null; }
}

document.addEventListener('click', function(e) {
    const modal = document.getElementById('tournament-modal');
    const box   = document.getElementById('tournament-hub-box');
    if (modal && modal.classList.contains('open') && box && !box.contains(e.target)) {
        closeTournamentHub();
    }
});

// Load all tournaments list, then show tabs
async function loadTournamentData(forceRefresh) {
    const content = document.getElementById('tournament-content');
    if (content) content.innerHTML = '<div style="padding:30px;text-align:center;color:#64748b;font-size:13px;">⏳ Loading tournaments...</div>';

    try {
        const res  = await fetchWithRetry(CONFIG.API_BASE_URL + '/tournament');
        const data = await res.json();

        if (data.status !== 'success') throw new Error('API error');

        _allTournaments = data.tournaments || [];

        // Update trophy dot
        const dot  = document.getElementById('t-trophy-dot');
        const tBtn = document.getElementById('tournament-trophy-btn');
        const hasActive = _allTournaments.some(t => ['registration_open','match_live'].includes(t.status));
        if (dot) dot.style.display = hasActive ? 'block' : 'none';
        if (tBtn) tBtn.classList.toggle('has-active', hasActive);

        if (_allTournaments.length === 0) {
            const badgeWrap = document.getElementById('t-status-badge-wrap');
            if (badgeWrap) badgeWrap.innerHTML = '';
            if (content) content.innerHTML =
                '<div class="t-lock-overlay">' +
                '<span class="t-lock-icon">🔒</span>' +
                '<p class="t-lock-title">No Active Tournament</p>' +
                '<p class="t-lock-sub">Stay tuned! Next tournament coming soon.</p>' +
                '</div>';
            return;
        }

        // If previously selected tid is still in list, keep it; else pick first
        const tidStillValid = _allTournaments.some(t => t.tournament_id === _selectedTid);
        if (!tidStillValid) _selectedTid = _allTournaments[0].tournament_id;

        _renderTournamentTabs();
        await loadTournamentById(_selectedTid, forceRefresh);
    } catch (err) {
        if (content) content.innerHTML =
            '<div style="padding:30px;text-align:center;">' +
            '<p style="color:#ef4444;font-size:13px;">⚠️ Could not load tournaments.</p>' +
            '<button onclick="loadTournamentData(true)" style="margin-top:12px;background:rgba(241,196,15,0.1);border:1px solid rgba(241,196,15,0.3);color:#f1c40f;border-radius:8px;padding:8px 20px;cursor:pointer;font-weight:700;">Retry</button></div>';
    }
}

// Render horizontal tab bar for all tournaments
function _renderTournamentTabs() {
    const tabWrap = document.getElementById('t-status-badge-wrap');
    if (!tabWrap) return;

    if (_allTournaments.length <= 1) {
        // Single tournament — show status badge as before
        const t = _allTournaments[0];
        if (!t) { tabWrap.innerHTML = ''; return; }
        const badgeCls   = { coming_soon:'coming-soon', registration_open:'reg-open', registration_closed:'reg-closed', match_live:'match-live', completed:'completed' }[t.status] || 'coming-soon';
        const badgeEmoji = { coming_soon:'🔜', registration_open:'✅', registration_closed:'🔒', match_live:'🔴', completed:'🏆' }[t.status] || '🔜';
        const badgeLabel = { coming_soon:'Coming Soon', registration_open:'Registration Open', registration_closed:'Registration Closed', match_live:'Match Live 🔴', completed:'Completed' }[t.status] || t.status;
        tabWrap.innerHTML = `<span class="t-status-badge ${badgeCls}">${badgeEmoji} ${badgeLabel}</span>`;
        return;
    }

    // Multiple tournaments — render scrollable tabs
    const statusEmoji = { coming_soon:'🔜', registration_open:'✅', registration_closed:'🔒', match_live:'🔴', completed:'🏆' };
    let tabsHtml = '<div id="t-tabs-bar" style="display:flex;gap:6px;overflow-x:auto;padding-bottom:2px;scrollbar-width:none;">';
    _allTournaments.forEach(t => {
        const isSelected = t.tournament_id === _selectedTid;
        const emoji = statusEmoji[t.status] || '🔜';
        tabsHtml += `<button
            onclick="event.stopPropagation(); selectTournamentTab('${_esc(t.tournament_id)}')"
            style="flex-shrink:0;padding:5px 12px;border-radius:20px;font-size:11px;font-weight:700;cursor:pointer;border:1.5px solid ${isSelected ? '#f1c40f' : 'rgba(255,255,255,0.12)'};background:${isSelected ? 'rgba(241,196,15,0.15)' : 'rgba(255,255,255,0.04)'};color:${isSelected ? '#f1c40f' : '#94a3b8'};white-space:nowrap;transition:all 0.2s;"
        >${emoji} ${_esc(t.title || t.tournament_id)}</button>`;
    });
    tabsHtml += '</div>';
    tabWrap.innerHTML = tabsHtml;
}

async function selectTournamentTab(tid) {
    _selectedTid = tid;
    _renderTournamentTabs();
    await loadTournamentById(tid, false);
}

// Load a specific tournament by ID
async function loadTournamentById(tid, forceRefresh) {
    const content = document.getElementById('tournament-content');

    // Check cache
    const cached = _tournamentCache[tid];
    if (!forceRefresh && cached && (Date.now() - cached.ts) < TOURNAMENT_CACHE_TTL) {
        _renderTournament(cached.tournament, cached.winners, cached.roundsData || null);
        return;
    }

    if (content) content.innerHTML = '<div style="padding:20px;text-align:center;color:#64748b;font-size:12px;">⏳ Loading...</div>';

    try {
        const uid = window._tgUser?.id;
        const [tRes, regRes] = await Promise.all([
            fetchWithRetry(`${CONFIG.API_BASE_URL}/tournament/${encodeURIComponent(tid)}`),
            uid ? fetchWithRetry(`${CONFIG.API_BASE_URL}/tournament/my_registration/${uid}?tournament_id=${encodeURIComponent(tid)}`) : Promise.resolve(null),
        ]);

        const tData   = await tRes.json();
        const regData = regRes ? await regRes.json() : { registered: false };

        _tournamentRegCache[tid] = (regData?.status === 'success') ? regData : { registered: false };

        // Fetch rounds data if tournament has multiple rounds or is live
        let roundsData = null;
        try {
            const t = tData.tournament;
            if (t && (parseInt(t.total_rounds||1) > 1 || t.status === 'match_live' || t.status === 'registration_closed' || t.status === 'registration_open')) {
                const rRes  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/tournament/${encodeURIComponent(tid)}/rounds?user_id=${encodeURIComponent(userId)}`);
                const rData = await rRes.json();
                if (rData.status === 'success') roundsData = rData;
            }
        } catch (_) { /* rounds fetch failure non-critical */ }

        if (tData.status === 'success') {
            _tournamentCache[tid] = { tournament: tData.tournament, winners: tData.winners || [], ts: Date.now(), roundsData };
        }

        _renderTournament(tData.tournament, tData.winners || [], roundsData);
    } catch (err) {
        if (content) content.innerHTML =
            '<div style="padding:20px;text-align:center;color:#ef4444;font-size:12px;">⚠️ Failed to load. <button onclick="loadTournamentById(\'' + _esc(tid) + '\',true)" style="color:#f1c40f;background:none;border:none;cursor:pointer;font-weight:700;">Retry</button></div>';
    }
}

function _renderTournament(t, winners, roundsData) {
    const content = document.getElementById('tournament-content');
    if (!content) return;

    if (!t) {
        content.innerHTML =
            '<div class="t-lock-overlay">' +
            '<span class="t-lock-icon">🔒</span>' +
            '<p class="t-lock-title">Tournament Not Found</p>' +
            '<p class="t-lock-sub">Stay tuned! More tournaments coming soon.</p>' +
            '</div>';
        return;
    }

    // If single tournament, update badge wrap to show status
    if (_allTournaments.length <= 1) {
        const badgeWrap = document.getElementById('t-status-badge-wrap');
        if (badgeWrap) {
            const badgeCls   = { coming_soon:'coming-soon', registration_open:'reg-open', registration_closed:'reg-closed', match_live:'match-live', completed:'completed' }[t.status] || 'coming-soon';
            const badgeEmoji = { coming_soon:'🔜', registration_open:'✅', registration_closed:'🔒', match_live:'🔴', completed:'🏆' }[t.status] || '🔜';
            const badgeLabel = { coming_soon:'Coming Soon', registration_open:'Registration Open', registration_closed:'Registration Closed', match_live:'Match Live 🔴', completed:'Completed' }[t.status] || t.status;
            badgeWrap.innerHTML = `<span class="t-status-badge ${badgeCls}">${badgeEmoji} ${badgeLabel}</span>`;
        }
    }

    let html = '';

    // ── Title + meta banner
    html += `
    <div style="padding:14px 16px 0;">
        <h2 style="color:#e2e8f0;font-size:18px;font-weight:800;margin:0 0 2px;">${_esc(t.title || 'Free Fire Tournament')}</h2>
        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;">
            ${t.mode ? `<span style="background:rgba(139,92,246,0.15);border:1px solid rgba(139,92,246,0.3);border-radius:8px;padding:3px 10px;font-size:11px;color:#a78bfa;font-weight:700;">🎮 ${_esc(t.mode)}</span>` : ''}
            ${t.map  ? `<span style="background:rgba(56,189,248,0.10);border:1px solid rgba(56,189,248,0.25);border-radius:8px;padding:3px 10px;font-size:11px;color:#38bdf8;font-weight:700;">🗺️ ${_esc(t.map)}</span>`  : ''}
            ${t.entry_fee == 0 ? `<span style="background:rgba(74,222,128,0.10);border:1px solid rgba(74,222,128,0.25);border-radius:8px;padding:3px 10px;font-size:11px;color:#4ade80;font-weight:700;">🆓 Free Entry</span>` : `<span style="background:rgba(241,196,15,0.10);border:1px solid rgba(241,196,15,0.25);border-radius:8px;padding:3px 10px;font-size:11px;color:#f1c40f;font-weight:700;">💰 ${t.entry_fee} 🪙 Entry</span>`}
        </div>
    </div>`;

    // ── Description (if set)
    if (t.description && t.description.trim()) {
        html += `
    <div style="margin:10px 16px 0;padding:10px 12px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.07);border-radius:10px;">
        <p style="font-size:12px;color:#94a3b8;margin:0;line-height:1.6;">${_esc(t.description)}</p>
    </div>`;
    }

    // ── Stats grid
    const _totalRnds   = parseInt(t.total_rounds  || 1);
    const _curRnd      = parseInt(t.current_round || 1);
    const _roundLabel  = t.status === 'match_live'
        ? `${_curRnd} / ${_totalRnds}`
        : `${_totalRnds}`;
    const _roundColor  = t.status === 'match_live' ? '#f87171' : '#38bdf8';
    const _roundTileLabel = t.status === 'match_live' ? '🔴 Current Round' : '🔄 Total Rounds';

    html += `
    <div class="t-stats-grid">
        <div class="t-stat-tile">
            <p class="t-lbl">👥 Registered</p>
            <p class="t-val" style="color:#4ade80;">${t.registered_count || 0}</p>
        </div>
        <div class="t-stat-tile">
            <p class="t-lbl">🎯 Slots Left</p>
            <p class="t-val" style="color:${(t.slots_remaining||0) <= 5 ? '#ef4444' : '#e2e8f0'};">${t.max_players > 0 ? (t.slots_remaining || 0) : '∞'}</p>
        </div>
        <div class="t-stat-tile">
            <p class="t-lbl">🏟️ Max Players</p>
            <p class="t-val">${t.max_players || '—'}</p>
        </div>
        <div class="t-stat-tile">
            <p class="t-lbl">${_roundTileLabel}</p>
            <p class="t-val" style="color:${_roundColor};font-weight:900;">${_roundLabel}</p>
        </div>
        ${t.date ? `<div class="t-stat-tile"><p class="t-lbl">📅 Date</p><p class="t-val" style="font-size:12px;">${_esc(t.date)}</p></div>` : ''}
        ${t.time ? `<div class="t-stat-tile"><p class="t-lbl">⏰ Time</p><p class="t-val" style="font-size:12px;">${_esc(t.time)}</p></div>` : ''}
        <div class="t-stat-tile">
            <p class="t-lbl">🏆 Prize</p>
            <p class="t-val" style="font-size:11px;color:#f1c40f;">GP Codes</p>
        </div>
    </div>`;

    html += '<div class="t-body">';

    // ── Prize pool section (dynamic)
    const _prizes    = t.prizes || [];
    const _rankEmoji = { 1: '🥇', 2: '🥈', 3: '🥉' };
    const _rankName  = { 1: 'Champion', 2: 'Runner Up', 3: 'Third Place' };

    if (_prizes.length > 0) {
        html += `<p style="font-size:11px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin:0 0 8px;">🎁 Prize Rewards</p>`;
        _prizes.forEach(p => {
            const rank = p.rank || 1;
            html += `
            <div class="t-prize-card">
                <span class="t-prize-medal">${_rankEmoji[rank] || '🏅'}</span>
                <div>
                    <p class="t-prize-name">${_esc(_rankName[rank] || p.label || 'Winner')}</p>
                    <p class="t-prize-reward">${_esc(p.prize || '—')}</p>
                </div>
            </div>`;
        });
    } else if (t.prize_pool) {
        html += `
        <p style="font-size:11px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin:0 0 8px;">🎁 Prize Pool</p>
        <div style="background:linear-gradient(135deg,rgba(241,196,15,0.08),rgba(251,146,60,0.05));border:1px solid rgba(241,196,15,0.25);border-radius:12px;padding:12px 14px;display:flex;align-items:center;gap:10px;">
            <span style="font-size:26px;">🏆</span>
            <p style="font-size:15px;font-weight:800;color:#f1c40f;margin:0;">${_esc(t.prize_pool)}</p>
        </div>`;
    }

    // ── Winners section (only if completed)
    if (t.status === 'completed' && winners && winners.length > 0) {
        html += `<div style="height:14px;"></div>`;
        html += `<p style="font-size:11px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin:0 0 8px;">🏆 Tournament Champions</p>`;
        const rankEmoji = { 1:'🥇', 2:'🥈', 3:'🥉' };
        const rankCls   = { 1:'rank-1', 2:'rank-2', 3:'rank-3' };
        const rankName  = { 1:'Champion', 2:'Runner Up', 3:'Third Place' };
        winners.forEach(w => {
            html += `
            <div class="t-winner-card ${rankCls[w.rank]||'rank-1'}">
                <span style="font-size:30px;flex-shrink:0;">${rankEmoji[w.rank]||'🏅'}</span>
                <div style="flex:1;min-width:0;">
                    <p style="font-size:13px;font-weight:800;color:#e2e8f0;margin:0;">${rankName[w.rank]||'Winner'}</p>
                    <p style="font-size:12px;color:#94a3b8;margin:2px 0 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">@${_esc(w.username)}</p>
                    <p style="font-size:11px;color:#f1c40f;margin:2px 0 0;font-weight:700;">${_esc(w.reward)}</p>
                </div>
            </div>`;
        });
    }

    // ── State-specific action area
    html += `<div style="height:14px;"></div>`;

    if (t.status === 'coming_soon') {
        html += `
        <div style="text-align:center;padding:24px 16px;background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.06);border-radius:14px;">
            <span class="t-lock-icon" style="font-size:38px;margin-bottom:10px;display:block;">⏳</span>
            <p style="font-size:16px;font-weight:800;color:#e2e8f0;margin:0 0 6px;">Coming Soon!</p>
            <p style="font-size:12px;color:#64748b;margin:0;">Registration will open soon. Stay tuned!</p>
        </div>`;

    } else if (t.status === 'registration_open') {
        const reg      = _tournamentRegCache[_selectedTid] || { registered: false };
        const _tmode   = (t.mode || 'Solo').toLowerCase();

        // ── Countdown: registration open → match live ──────────────────────
        if (t.match_time_iso) {
            html += `
            <div id="match-countdown-box" style="text-align:center;padding:12px 14px;background:rgba(241,196,15,0.08);border:1px solid rgba(241,196,15,0.3);border-radius:12px;margin-bottom:12px;">
                <p style="font-size:11px;color:#94a3b8;margin:0 0 4px;text-transform:uppercase;letter-spacing:0.5px;">⏱ Match Starts In</p>
                <p id="match-countdown-timer" style="font-size:20px;font-weight:800;color:#f1c40f;margin:0;font-variant-numeric:tabular-nums;letter-spacing:1px;">--:--:--</p>
            </div>`;
        }

        // ── Round info banner at top of registration section
        const _roTotalRnds = parseInt(t.total_rounds || 1);
        if (_roTotalRnds > 1) {
            html += `
            <div style="padding:10px 14px;background:rgba(56,189,248,0.07);border:1px solid rgba(56,189,248,0.25);border-radius:12px;margin-bottom:12px;display:flex;align-items:center;gap:10px;">
                <span style="font-size:22px;">🔄</span>
                <div>
                    <p style="font-size:13px;font-weight:800;color:#38bdf8;margin:0;">${_roTotalRnds} Rounds Tournament</p>
                    <p style="font-size:11px;color:#64748b;margin:2px 0 0;">Har round ke liye alag Room ID milega. Sab rounds mein khelo!</p>
                </div>
            </div>`;
        }
        const isSquad  = _tmode === 'squad';
        const isDuo    = _tmode === 'duo';
        const isTeam   = isSquad || isDuo;

        if (reg && reg.registered) {
            // ── Already registered ──
            const rd = reg.data || {};
            let regDetails = '';
            if (rd.registration_type === 'squad' || rd.registration_type === 'duo' || isTeam) {
                const members = rd.members || [];
                const memberRows = members.map((m, i) =>
                    `<p style="font-size:11px;color:#94a3b8;margin:2px 0;">
                        <span style="color:#64748b;">M${i+1}:</span>
                        <b style="color:#e2e8f0;">${_esc(m.ff_nickname||'')}</b>
                        <span style="color:#475569;"> · ${_esc(m.ff_uid||'')}</span>
                    </p>`
                ).join('');
                const teamIdRow = rd.team_id
                    ? `<p style="font-size:12px;margin:6px 0 4px;"><span style="color:#a78bfa;font-weight:700;">🛡️ Team ID:</span> <b style="color:#e2e8f0;letter-spacing:1px;">${_esc(rd.team_id)}</b></p>`
                    : '';
                regDetails = `
                    ${teamIdRow}
                    <p style="font-size:13px;font-weight:800;color:#f1c40f;margin:4px 0 6px;">🏟️ Team: ${_esc(rd.team_name||'')}</p>
                    <div style="background:rgba(0,0,0,0.2);border-radius:8px;padding:8px 10px;margin-bottom:4px;">${memberRows}</div>`;
            } else {
                regDetails = `<p style="font-size:12px;color:#64748b;margin:4px 0 0;">FF UID: <b style="color:#e2e8f0;">${_esc(rd.ff_uid||'')}</b> &nbsp;·&nbsp; Nick: <b style="color:#e2e8f0;">${_esc(rd.ff_nickname||'')}</b></p>`;
            }
            html += `
            <div class="t-registered-badge">
                <span style="font-size:28px;display:block;margin-bottom:6px;">✅</span>
                <p style="font-size:15px;font-weight:800;color:#4ade80;margin:0 0 2px;">You're Registered!</p>
                ${regDetails}
                <p style="font-size:11px;color:#475569;margin-top:6px;">Registered on ${_esc(rd.registered_at||'')}</p>
            </div>
            <p style="font-size:11px;color:#64748b;text-align:center;margin-top:8px;">Room credentials will be shared before the match. Stay alert!</p>`;

        } else {
            // ── Registration form ──
            const fee = t.entry_fee || 0;
            const feeBox = fee > 0
                ? `<div style="background:rgba(241,196,15,0.07);border:1px solid rgba(241,196,15,0.30);border-radius:12px;padding:12px 14px;margin-bottom:12px;display:flex;align-items:center;gap:10px;">
                    <span style="font-size:24px;">💰</span>
                    <div>
                        <p style="font-size:13px;font-weight:800;color:#f1c40f;margin:0;">Entry Fee: ${fee} Coins</p>
                        <p style="font-size:11px;color:#94a3b8;margin:3px 0 0;">Yeh coins aapke balance se automatically kaat liye jayenge registration ke time.</p>
                    </div>
                  </div>`
                : `<div style="background:rgba(74,222,128,0.05);border:1px solid rgba(74,222,128,0.15);border-radius:12px;padding:12px;margin-bottom:12px;">
                    <p style="font-size:12px;color:#94a3b8;margin:0;">🎮 Enter your Free Fire details to join the tournament.</p>
                   </div>`;

            const btnLabel = fee > 0
                ? (isDuo ? `🤝 Register Duo & Pay ${fee} 🪙` : isSquad ? `🛡️ Register Squad & Pay ${fee} 🪙` : `🎯 Register & Pay ${fee} 🪙`)
                : (isDuo ? '🤝 Register Duo' : isSquad ? '🛡️ Register Squad' : '🎯 Register for Tournament');

            if (isDuo) {
                // ── Duo form: Team Name + exactly 2 members ──
                const duoFields = [1,2].map(i => `
                <div style="margin-bottom:10px;">
                    <p style="font-size:11px;font-weight:700;color:#64748b;margin:0 0 5px;">
                        ${i === 1 ? '👑 Player 1 — You (Leader)' : '👤 Player 2 — Partner'}
                    </p>
                    <div style="display:flex;gap:6px;">
                        <input class="t-input" id="t-m${i}-uid"  type="text" inputmode="numeric"
                               placeholder="FF UID" style="flex:1;margin:0;" maxlength="15" />
                        <input class="t-input" id="t-m${i}-nick" type="text"
                               placeholder="FF Name" style="flex:1.4;margin:0;" maxlength="30" />
                    </div>
                </div>`).join('');

                html += `
                <p style="font-size:11px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin:0 0 10px;">🤝 Register Duo</p>
                ${feeBox}
                <input class="t-input" id="t-team-name" type="text" placeholder="Team Name (e.g. DeathDuo)" maxlength="30" />
                <p style="font-size:11px;font-weight:700;color:#64748b;margin:8px 0 6px;border-top:1px solid rgba(255,255,255,0.05);padding-top:8px;">👥 Both Players</p>
                ${duoFields}
                <p id="t-reg-msg" style="font-size:12px;color:#94a3b8;min-height:18px;margin:0 0 10px;text-align:center;"></p>
                <button class="t-reg-btn" id="t-reg-btn" onclick="registerForTournament()">${btnLabel}</button>`;

            } else if (isSquad) {
                // ── Squad form: Team Name + 4 members (2 required, 2 optional) ──
                const memberFields = [1,2,3,4].map(i => `
                <div style="margin-bottom:10px;">
                    <p style="font-size:11px;font-weight:700;color:#64748b;margin:0 0 5px;">
                        ${i === 1 ? '👑 Member 1 — You (Leader)' : `👤 Member ${i}${i > 2 ? ' <span style="color:#475569;">(Optional)</span>' : ''}`}
                    </p>
                    <div style="display:flex;gap:6px;">
                        <input class="t-input" id="t-m${i}-uid"  type="text" inputmode="numeric"
                               placeholder="FF UID" style="flex:1;margin:0;" maxlength="15" />
                        <input class="t-input" id="t-m${i}-nick" type="text"
                               placeholder="FF Name" style="flex:1.4;margin:0;" maxlength="30" />
                    </div>
                </div>`).join('');

                html += `
                <p style="font-size:11px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin:0 0 10px;">🛡️ Register Squad</p>
                ${feeBox}
                <input class="t-input" id="t-team-name" type="text" placeholder="Team Name (e.g. Alpha Squad)" maxlength="30" />
                <p style="font-size:11px;font-weight:700;color:#64748b;margin:8px 0 6px;border-top:1px solid rgba(255,255,255,0.05);padding-top:8px;">👥 Squad Members (2–4)</p>
                ${memberFields}
                <p id="t-reg-msg" style="font-size:12px;color:#94a3b8;min-height:18px;margin:0 0 10px;text-align:center;"></p>
                <button class="t-reg-btn" id="t-reg-btn" onclick="registerForTournament()">${btnLabel}</button>`;
            } else {
                // ── Solo form ──
                html += `
                <p style="font-size:11px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin:0 0 10px;">📝 Register Now</p>
                ${feeBox}
                <input class="t-input" id="t-ff-uid"      type="text" inputmode="numeric" placeholder="Free Fire UID (e.g. 123456789)" maxlength="20" />
                <input class="t-input" id="t-ff-nickname" type="text" placeholder="FF Name" maxlength="30" />
                <p id="t-reg-msg" style="font-size:12px;color:#94a3b8;min-height:18px;margin:0 0 10px;text-align:center;"></p>
                <button class="t-reg-btn" id="t-reg-btn" onclick="registerForTournament()">${btnLabel}</button>`;
            }
        }

    } else if (t.status === 'registration_closed') {
        const reg = _tournamentRegCache[_selectedTid] || { registered: false };
        html += `
        <div style="text-align:center;padding:24px 16px;background:rgba(251,146,60,0.05);border:1px solid rgba(251,146,60,0.20);border-radius:14px;">
            <span class="t-lock-icon" style="font-size:38px;margin-bottom:10px;display:block;">🔒</span>
            <p style="font-size:16px;font-weight:800;color:#fb923c;margin:0 0 6px;">Registration Closed</p>
            <p style="font-size:12px;color:#64748b;margin:0;">Registration period is over. Match starting soon!</p>
        </div>`;

        // ── Round info banner (registration closed — match aane wala hai)
        const _rcTotalRnds = parseInt(t.total_rounds || 1);
        if (_rcTotalRnds > 1) {
            html += `
            <div style="margin-top:10px;padding:12px 14px;background:rgba(56,189,248,0.07);border:1px solid rgba(56,189,248,0.25);border-radius:12px;display:flex;align-items:center;gap:12px;">
                <span style="font-size:26px;">🔄</span>
                <div>
                    <p style="font-size:13px;font-weight:800;color:#38bdf8;margin:0;">Is tournament mein <b>${_rcTotalRnds} rounds</b> honge</p>
                    <p style="font-size:11px;color:#64748b;margin:3px 0 0;">Har round ke liye alag Room ID & Password milega.</p>
                </div>
            </div>`;
        }
        // BUG FIX T4: registration_closed mein Team ID nahi dikh rahi thi.
        // Yahi woh window hai jab user ko apna Team ID confirm karna hota hai
        // match shuru hone se pehle. Ab Team ID + registration type bhi dikhate hain.
        if (reg && reg.registered) {
            const rd4 = reg.data || {};
            const teamIdBlock4 = rd4.team_id
                ? `<div style="margin-top:8px;display:flex;align-items:center;justify-content:space-between;
                               padding:8px 12px;background:rgba(139,92,246,0.10);
                               border:1px solid rgba(139,92,246,0.28);border-radius:10px;">
                       <div>
                           <p style="font-size:10px;color:#a78bfa;margin:0 0 2px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">🛡️ Your Team ID</p>
                           <p style="font-size:22px;font-weight:900;color:#e2e8f0;margin:0;letter-spacing:1px;">${_esc(rd4.team_id)}</p>
                       </div>
                       <button onclick="navigator.clipboard.writeText('${_esc(rd4.team_id)}').then(()=>showToast('Team ID copied! ✅','success'))"
                           style="background:rgba(139,92,246,0.15);border:1px solid rgba(139,92,246,0.35);
                                  border-radius:8px;padding:6px 12px;color:#a78bfa;font-size:11px;font-weight:700;cursor:pointer;">📋 Copy</button>
                   </div>`
                : '';
            const regTypeLabel4 = rd4.team_name
                ? `<span style="font-size:11px;color:#64748b;">Team: <b style="color:#94a3b8;">${_esc(rd4.team_name)}</b></span>`
                : (rd4.ff_nickname ? `<span style="font-size:11px;color:#64748b;">FF Name: <b style="color:#94a3b8;">${_esc(rd4.ff_nickname)}</b></span>` : '');
            html += `
            <div class="t-registered-badge" style="margin-top:10px;">
                <p style="font-size:13px;font-weight:800;color:#4ade80;margin:0 0 4px;">✅ You are registered!</p>
                ${regTypeLabel4}
                ${teamIdBlock4}
                <p style="font-size:11px;color:#64748b;margin-top:8px;">Room ID & Password will be shared before match time. Keep your Team ID ready!</p>
            </div>`;
        }

    } else if (t.status === 'match_live') {
        const reg = _tournamentRegCache[_selectedTid] || { registered: false };

        // Determine current round room credentials
        // Priority: current live round > global tournament room
        let roomId   = t.room_id       || null;
        let roomPass = t.room_password || null;
        let currentRoundNo   = t.current_round || 1;
        let totalRoundsCount = t.total_rounds  || 1;

        if (roundsData && roundsData.rounds) {
            const liveRound = roundsData.rounds.find(r => r.status === 'live');
            if (liveRound) {
                if (liveRound.room_id)       roomId   = liveRound.room_id;
                if (liveRound.room_password) roomPass = liveRound.room_password;
                currentRoundNo = liveRound.round_no;
            }
            totalRoundsCount = roundsData.total_rounds || totalRoundsCount;
        }

        // ── Live banner
        const roundLabel = totalRoundsCount > 1
            ? ` · Round ${currentRoundNo}/${totalRoundsCount}`
            : '';
        html += `
        <div style="text-align:center;padding:16px;background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.28);border-radius:14px;margin-bottom:12px;">
            <span style="font-size:40px;display:block;margin-bottom:6px;" class="t-lock-icon">🔥</span>
            <p style="font-size:18px;font-weight:900;color:#f87171;margin:0 0 4px;letter-spacing:0.5px;">MATCH IS LIVE!</p>
            ${totalRoundsCount > 1
                ? `<p style="font-size:15px;font-weight:800;color:#fbbf24;margin:4px 0;">Round ${currentRoundNo} / ${totalRoundsCount} chal raha hai 🎯</p>`
                : `<p style="font-size:13px;font-weight:700;color:#fbbf24;margin:4px 0;">Single Round Match 🎯</p>`}
            <p style="font-size:12px;color:#94a3b8;margin:4px 0 0;">The battle has begun. Good luck to all players!</p>
        </div>`;

        // ── Rounds progress bar (if multi-round)
        // BUG FIX T2: Backend "completed" save karta hai, "ended" nahi.
        // Isliye ab dono keys ko same color/emoji map kiya — legacy "ended" bhi support hoga.
        if (roundsData && roundsData.rounds && totalRoundsCount > 1) {
            const _isDone  = rs => rs === 'completed' || rs === 'ended';
            const statusColor = rs =>
                rs === 'live'       ? 'rgba(239,68,68,0.12)'     :
                _isDone(rs)         ? 'rgba(74,222,128,0.08)'    :
                                      'rgba(255,255,255,0.04)';
            const statusBorder = rs =>
                rs === 'live'       ? 'rgba(239,68,68,0.4)'      :
                _isDone(rs)         ? 'rgba(74,222,128,0.25)'    :
                                      'rgba(255,255,255,0.08)';
            const statusEmoji = rs =>
                rs === 'live'       ? '🔴' :
                _isDone(rs)         ? '✅' :
                                      '⏳';
            let roundsHtml = '<div style="display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;">';
            for (let rn = 1; rn <= totalRoundsCount; rn++) {
                const rDoc = roundsData.rounds.find(r => r.round_no === rn) || { status: 'pending', round_no: rn };
                const rs   = rDoc.status || 'pending';
                roundsHtml += `<div style="flex:1;min-width:60px;text-align:center;padding:6px 4px;border-radius:10px;
                    background:${statusColor(rs)};border:1px solid ${statusBorder(rs)};">
                    <p style="font-size:10px;color:#64748b;margin:0;">Round ${rn}</p>
                    <p style="font-size:14px;margin:2px 0 0;">${statusEmoji(rs)}</p>
                </div>`;
            }
            roundsHtml += '</div>';
            html += roundsHtml;
        }

        if (reg && reg.registered) {
            // ── Room credentials dashboard (only for registered users)
            const roundTitle = totalRoundsCount > 1
                ? `🔑 Round ${currentRoundNo} Room Details`
                : '🔑 Your Room Details';

            html += `
            <div style="background:linear-gradient(135deg,rgba(241,196,15,0.08),rgba(251,146,60,0.06));border:1.5px solid rgba(241,196,15,0.35);border-radius:16px;padding:16px;margin-bottom:4px;">
                <p style="font-size:11px;font-weight:800;color:#f1c40f;text-transform:uppercase;letter-spacing:0.8px;margin:0 0 12px;text-align:center;">${_esc(roundTitle)}</p>

                <div style="background:rgba(0,0,0,0.30);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:12px 14px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <p style="font-size:10px;color:#64748b;margin:0 0 3px;text-transform:uppercase;letter-spacing:0.5px;">🆔 Room ID</p>
                        <p id="t-room-id-val" style="font-size:20px;font-weight:900;color:#e2e8f0;letter-spacing:2px;margin:0;">${roomId ? _esc(roomId) : '<span style="color:#475569;font-size:13px;font-weight:400;">Not set yet</span>'}</p>
                    </div>
                    ${roomId ? `<button onclick="navigator.clipboard.writeText('${_esc(roomId)}').then(()=>showToast('Room ID copied! ✅','success'))" style="background:rgba(241,196,15,0.15);border:1px solid rgba(241,196,15,0.35);border-radius:8px;padding:6px 12px;color:#f1c40f;font-size:11px;font-weight:700;cursor:pointer;">📋 Copy</button>` : ''}
                </div>

                <div style="background:rgba(0,0,0,0.30);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <p style="font-size:10px;color:#64748b;margin:0 0 3px;text-transform:uppercase;letter-spacing:0.5px;">🔑 Password</p>
                        <p id="t-room-pass-val" style="font-size:20px;font-weight:900;color:#e2e8f0;letter-spacing:2px;margin:0;">${roomPass ? _esc(roomPass) : '<span style="color:#475569;font-size:13px;font-weight:400;">Not set yet</span>'}</p>
                    </div>
                    ${roomPass ? `<button onclick="navigator.clipboard.writeText('${_esc(roomPass)}').then(()=>showToast('Password copied! ✅','success'))" style="background:rgba(241,196,15,0.15);border:1px solid rgba(241,196,15,0.35);border-radius:8px;padding:6px 12px;color:#f1c40f;font-size:11px;font-weight:700;cursor:pointer;">📋 Copy</button>` : ''}
                </div>

                ${(!roomId || !roomPass) ? `
                <p style="font-size:11px;color:#475569;text-align:center;margin:10px 0 0;">⏳ Room credentials will appear here once admin sets them. Keep refreshing!</p>
                ` : `
                <p style="font-size:11px;color:#4ade80;text-align:center;margin:10px 0 0;font-weight:700;">✅ All set! Join the room and good luck 🎯</p>
                `}
            </div>`;

            // ── Tournament ID + Team ID display (for squad/duo registrations)
            const rd = reg.data || {};
            if (rd.team_id) {
                html += `
                <div style="margin-top:8px;padding:10px 14px;background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.25);border-radius:12px;display:flex;align-items:center;justify-content:space-between;">
                    <div>
                        <p style="font-size:10px;color:#fbbf24;margin:0 0 2px;text-transform:uppercase;letter-spacing:0.5px;font-weight:700;">🏆 Tournament ID</p>
                        <p style="font-size:22px;font-weight:900;color:#e2e8f0;margin:0;letter-spacing:1px;">${_esc(_selectedTid || '')}</p>
                    </div>
                    ${_selectedTid ? `<button onclick="navigator.clipboard.writeText('${_esc(_selectedTid)}').then(()=>showToast('Tournament ID copied! ✅','success'))" style="background:rgba(251,191,36,0.15);border:1px solid rgba(251,191,36,0.35);border-radius:8px;padding:6px 12px;color:#fbbf24;font-size:11px;font-weight:700;cursor:pointer;">📋 Copy</button>` : ''}
                </div>
                <div style="margin-top:6px;padding:10px 14px;background:rgba(139,92,246,0.08);border:1px solid rgba(139,92,246,0.25);border-radius:12px;display:flex;align-items:center;justify-content:space-between;">
                    <div>
                        <p style="font-size:10px;color:#a78bfa;margin:0 0 2px;text-transform:uppercase;letter-spacing:0.5px;font-weight:700;">🛡️ Your Team ID</p>
                        <p style="font-size:22px;font-weight:900;color:#e2e8f0;margin:0;letter-spacing:1px;">${_esc(rd.team_id)}</p>
                    </div>
                    <button onclick="navigator.clipboard.writeText('${_esc(rd.team_id)}').then(()=>showToast('Team ID copied! ✅','success'))"
                        style="background:rgba(139,92,246,0.15);border:1px solid rgba(139,92,246,0.35);border-radius:8px;padding:6px 12px;color:#a78bfa;font-size:11px;font-weight:700;cursor:pointer;">📋 Copy</button>
                </div>`;
            }
        } else {
            // Not registered — show a message
            html += `
            <div style="text-align:center;padding:14px;background:rgba(239,68,68,0.05);border:1px dashed rgba(239,68,68,0.25);border-radius:12px;">
                <p style="font-size:13px;color:#f87171;margin:0;font-weight:700;">❌ You are not registered</p>
                <p style="font-size:11px;color:#64748b;margin:6px 0 0;">Registration was required before match start. Wait for the next tournament!</p>
            </div>`;
        }

    } else if (t.status === 'completed' && (!winners || winners.length === 0)) {
        html += `
        <div style="text-align:center;padding:20px;background:rgba(241,196,15,0.05);border:1px solid rgba(241,196,15,0.18);border-radius:14px;">
            <p style="font-size:15px;font-weight:800;color:#f1c40f;margin:0 0 4px;">🏆 Tournament Completed</p>
            <p style="font-size:12px;color:#64748b;margin:0;">Winners will be announced shortly. Check back soon!</p>
        </div>`;
    }

    html += `
    <div style="height:14px;"></div>
    <button onclick="loadTournamentById('${_esc(_selectedTid)}', true)" style="width:100%;padding:10px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:10px;color:#64748b;font-size:12px;cursor:pointer;">↻ Refresh</button>
    </div>`;  // close t-body

    content.innerHTML = html;
    // Countdown only applies while registration is open; any other status
    // (registration_closed, match_live, etc.) stops it — matches "registration
    // open tak → match live hone tak" requirement.
    _startMatchCountdown(t.status === 'registration_open' ? t.match_time_iso : null);
}

let _matchCountdownInterval = null;

function _startMatchCountdown(matchTimeIso) {
    if (_matchCountdownInterval) { clearInterval(_matchCountdownInterval); _matchCountdownInterval = null; }
    if (!matchTimeIso) return;

    const target = new Date(matchTimeIso).getTime();
    if (isNaN(target)) return;

    const tick = () => {
        const el = document.getElementById('match-countdown-timer');
        if (!el) { clearInterval(_matchCountdownInterval); _matchCountdownInterval = null; return; }
        const diff = target - Date.now();
        if (diff <= 0) {
            el.textContent = '🔴 Match is starting...';
            clearInterval(_matchCountdownInterval);
            _matchCountdownInterval = null;
            return;
        }
        const totalSec = Math.floor(diff / 1000);
        const days = Math.floor(totalSec / 86400);
        const hrs  = Math.floor((totalSec % 86400) / 3600);
        const mins = Math.floor((totalSec % 3600) / 60);
        const secs = totalSec % 60;
        const pad  = n => String(n).padStart(2, '0');
        el.textContent = days > 0
            ? `${days}d ${pad(hrs)}h ${pad(mins)}m ${pad(secs)}s`
            : `${pad(hrs)}:${pad(mins)}:${pad(secs)}`;
    };
    tick();
    _matchCountdownInterval = setInterval(tick, 1000);
}

// Per-tournament leaderboard — every tournament has its own separate leaderboard.
// Access rules:
//   • completed tournaments  → public final leaderboard, anyone can view
//   • all other statuses     → user must be registered to view; shows join-prompt otherwise
async function openTournamentLeaderboard(tid) {
    const content = document.getElementById('tournament-content');
    if (content) content.innerHTML = '<div style="padding:20px;text-align:center;color:#64748b;font-size:12px;">⏳ Loading leaderboard...</div>';

    // ── Frontend registration gate ────────────────────────────────────────
    // PUBLIC  (no gate): match_live  — live scoreboard, sabhi dekh sakte hain
    //                    completed   — final results bhi public
    // GATED   (join needed): registration_open / registration_closed / coming_soon
    const cachedTournament = (_tournamentCache[tid] || {}).tournament;
    const _tStatus    = cachedTournament?.status || '';
    const isCompleted = _tStatus === 'completed';
    const isLive      = _tStatus === 'match_live';
    const isPublic    = isCompleted || isLive;   // no gate for live + completed

    if (!isPublic) {
        const reg = _tournamentRegCache[tid] || { registered: false };
        if (!reg.registered) {
            // User hasn't joined — show join-prompt instead of leaderboard
            if (content) content.innerHTML = `
                <div class="t-body">
                    <div style="text-align:center;padding:36px 20px;">
                        <span style="font-size:46px;display:block;margin-bottom:14px;">🔒</span>
                        <p style="font-size:16px;font-weight:800;color:#e2e8f0;margin:0 0 8px;">Leaderboard Locked</p>
                        <p style="font-size:13px;color:#94a3b8;margin:0 0 20px;line-height:1.6;">
                            ⚠️ Join this tournament to view the leaderboard.
                        </p>
                        <button onclick="loadTournamentById('${_esc(tid)}', false)"
                            style="width:100%;padding:11px;background:linear-gradient(135deg,#f1c40f,#e67e22);
                                   color:#000;font-size:13px;font-weight:800;border:none;
                                   border-radius:12px;cursor:pointer;margin-bottom:8px;">
                            🏆 Register Now
                        </button>
                        <button onclick="loadTournamentById('${_esc(tid)}', false)"
                            style="width:100%;padding:10px;background:rgba(255,255,255,0.04);
                                   border:1px solid rgba(255,255,255,0.08);border-radius:10px;
                                   color:#64748b;font-size:12px;cursor:pointer;">
                            ← Back to Tournament
                        </button>
                    </div>
                </div>`;
            return;
        }
    }
    // ─────────────────────────────────────────────────────────────────────────

    try {
        // Pass user_id so the backend can also enforce the registration gate
        const uid    = window._tgUser?.id || '';
        const uidQs  = uid ? `?user_id=${encodeURIComponent(uid)}` : '';
        const res    = await fetchWithRetry(
            `${CONFIG.API_BASE_URL}/tournament/${encodeURIComponent(tid)}/leaderboard${uidQs}`
        );
        const data   = await res.json();

        // Backend returned not_joined (edge-case: cache was stale)
        if (data.status === 'not_joined') {
            if (content) content.innerHTML = `
                <div class="t-body">
                    <div style="text-align:center;padding:36px 20px;">
                        <span style="font-size:46px;display:block;margin-bottom:14px;">🔒</span>
                        <p style="font-size:16px;font-weight:800;color:#e2e8f0;margin:0 0 8px;">Leaderboard Locked</p>
                        <p style="font-size:13px;color:#94a3b8;margin:0 0 20px;line-height:1.6;">
                            ⚠️ Join this tournament to view the leaderboard.
                        </p>
                        <button onclick="loadTournamentById('${_esc(tid)}', false)"
                            style="width:100%;padding:11px;background:linear-gradient(135deg,#f1c40f,#e67e22);
                                   color:#000;font-size:13px;font-weight:800;border:none;
                                   border-radius:12px;cursor:pointer;margin-bottom:8px;">
                            🏆 Register Now
                        </button>
                        <button onclick="loadTournamentById('${_esc(tid)}', false)"
                            style="width:100%;padding:10px;background:rgba(255,255,255,0.04);
                                   border:1px solid rgba(255,255,255,0.08);border-radius:10px;
                                   color:#64748b;font-size:12px;cursor:pointer;">
                            ← Back to Tournament
                        </button>
                    </div>
                </div>`;
            return;
        }

        if (data.status !== 'success') throw new Error(data.message || 'API error');

        const board       = data.leaderboard || [];
        const totalRounds = data.total_rounds || 1;
        const rankEmoji   = { 1: '🥇', 2: '🥈', 3: '🥉' };

        // Header label
        const lbTitle = data.is_completed
            ? '🏆 Final Leaderboard'
            : data.is_live
                ? `🔴 Live Leaderboard · Round ${data.current_round || 0}/${totalRounds}`
                : `📊 Tournament Leaderboard · ${data.current_round || 0}/${totalRounds} rounds played`;

        let html = '<div class="t-body">';
        html += `<p style="font-size:11px;font-weight:800;color:#64748b;text-transform:uppercase;
                           letter-spacing:0.8px;margin:0 0 12px;">${lbTitle}</p>`;

        // Status banner — completed or live
        if (data.is_completed) {
            html += `
            <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;
                        background:rgba(241,196,15,0.08);border:1px solid rgba(241,196,15,0.25);
                        border-radius:12px;margin-bottom:12px;">
                <span style="font-size:22px;">🏆</span>
                <div>
                    <p style="font-size:13px;font-weight:800;color:#f1c40f;margin:0;">Tournament Completed</p>
                    <p style="font-size:11px;color:#64748b;margin:2px 0 0;">Final standings — these results are official.</p>
                </div>
            </div>`;
        } else if (data.is_live) {
            html += `
            <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;
                        background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.28);
                        border-radius:12px;margin-bottom:12px;">
                <span style="font-size:22px;animation:lock-pulse 1.8s ease-in-out infinite;">🔴</span>
                <div>
                    <p style="font-size:13px;font-weight:800;color:#f87171;margin:0;">Match is Live!</p>
                    <p style="font-size:11px;color:#64748b;margin:2px 0 0;">Scores update after each round is submitted by admin.</p>
                </div>
            </div>`;
        }

        if (board.length === 0) {
            // Different empty message for live vs pre-match
            const emptyMsg = data.is_live
                ? `<div style="text-align:center;padding:30px 16px;">
                        <span style="font-size:40px;display:block;margin-bottom:12px;">⏳</span>
                        <p style="color:#e2e8f0;font-size:14px;font-weight:700;margin:0 0 6px;">Match in Progress...</p>
                        <p style="color:#64748b;font-size:12px;margin:0;line-height:1.6;">
                            Round results will appear here once admin submits scores.<br>
                            Tap <b style="color:#94a3b8;">↻ Refresh</b> after each round!
                        </p>
                   </div>`
                : `<div style="text-align:center;padding:30px;color:#475569;font-size:13px;">
                        No results yet. Results will appear after each round.
                   </div>`;
            html += emptyMsg;
        } else {
            // Round column headers
            let roundCols = '';
            for (let rn = 1; rn <= totalRounds; rn++) {
                roundCols += `<span style="flex:0.8;text-align:center;font-size:10px;color:#64748b;font-weight:700;">R${rn}</span>`;
            }
            html += `
            <div style="display:flex;align-items:center;gap:6px;padding:4px 8px;margin-bottom:4px;">
                <span style="width:28px;"></span>
                <span style="flex:2;font-size:10px;color:#64748b;font-weight:700;">TEAM</span>
                ${roundCols}
                <span style="flex:1;text-align:center;font-size:10px;color:#64748b;font-weight:700;">KILLS</span>
                <span style="flex:1;text-align:center;font-size:10px;color:#f1c40f;font-weight:700;">PTS</span>
            </div>`;

            board.forEach(row => {
                const pos     = row.position || '?';
                const isTop3  = pos <= 3;
                const teamBg  = pos === 1 ? 'rgba(241,196,15,0.07)' : pos === 2 ? 'rgba(148,163,184,0.06)' : pos === 3 ? 'rgba(180,83,9,0.07)' : 'rgba(255,255,255,0.025)';
                const teamBdr = pos === 1 ? 'rgba(241,196,15,0.25)' : pos === 2 ? 'rgba(148,163,184,0.18)' : pos === 3 ? 'rgba(180,83,9,0.20)' : 'rgba(255,255,255,0.06)';

                let roundPts = '';
                for (let rn = 1; rn <= totalRounds; rn++) {
                    const pts = row.rounds?.[rn] ?? '—';
                    roundPts += `<span style="flex:0.8;text-align:center;font-size:11px;color:${typeof pts === 'number' ? '#e2e8f0' : '#475569'};">${pts}</span>`;
                }
                html += `
                <div style="display:flex;align-items:center;gap:6px;padding:8px;
                             background:${teamBg};border:1px solid ${teamBdr};
                             border-radius:10px;margin-bottom:5px;">
                    <span style="width:28px;font-size:${isTop3 ? '18px' : '12px'};text-align:center;">${rankEmoji[pos] || ('#' + pos)}</span>
                    <div style="flex:2;min-width:0;">
                        <p style="font-size:12px;font-weight:800;color:#e2e8f0;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${_esc(row.team_name || row.team_id)}</p>
                        <p style="font-size:10px;color:#64748b;margin:1px 0 0;">${_esc(row.team_id)}</p>
                    </div>
                    ${roundPts}
                    <span style="flex:1;text-align:center;font-size:12px;color:#94a3b8;font-weight:700;">${row.total_kills ?? 0}</span>
                    <span style="flex:1;text-align:center;font-size:13px;color:#f1c40f;font-weight:900;">${row.total_points ?? 0}</span>
                </div>`;
            });
        }

        html += `
        <div style="height:14px;"></div>
        <button onclick="loadTournamentById('${_esc(tid)}', false)"
            style="width:100%;padding:10px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);
                   border-radius:10px;color:#64748b;font-size:12px;cursor:pointer;">← Back to Tournament</button>
        </div>`;

        if (content) content.innerHTML = html;
    } catch (err) {
        if (content) content.innerHTML =
            `<div style="padding:20px;text-align:center;">
                <p style="color:#ef4444;font-size:13px;">⚠️ Could not load leaderboard.</p>
                <button onclick="openTournamentLeaderboard('${_esc(tid)}')"
                    style="margin-top:10px;background:rgba(241,196,15,0.1);border:1px solid rgba(241,196,15,0.3);
                           color:#f1c40f;border-radius:8px;padding:8px 20px;cursor:pointer;font-weight:700;">Retry</button>
                <button onclick="loadTournamentById('${_esc(tid)}', false)"
                    style="margin-top:8px;display:block;width:100%;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);
                           border-radius:10px;color:#64748b;font-size:12px;padding:10px;cursor:pointer;">← Back</button>
             </div>`;
    }
}

// ============================================================
// PUBLIC TOURNAMENT LEADERBOARD  (Search by Tournament ID + Team ID)
// ============================================================

// ── Public Leaderboard session cache (10 min) ────────────────
const _LB_CACHE_KEY = 'pub_lb_cache';
const _LB_CACHE_TTL = 10 * 60 * 1000; // 10 minutes in ms

function _lbSaveCache(tid, teamId) {
    try {
        sessionStorage.setItem(_LB_CACHE_KEY, JSON.stringify({
            tid, teamId, ts: Date.now()
        }));
    } catch(_) {}
}

function _lbLoadCache() {
    try {
        const raw = sessionStorage.getItem(_LB_CACHE_KEY);
        if (!raw) return null;
        const obj = JSON.parse(raw);
        if (!obj || !obj.tid || !obj.teamId) return null;
        if (Date.now() - obj.ts > _LB_CACHE_TTL) {
            sessionStorage.removeItem(_LB_CACHE_KEY);
            return null;
        }
        return obj;
    } catch(_) { return null; }
}

function _lbClearCache() {
    try { sessionStorage.removeItem(_LB_CACHE_KEY); } catch(_) {}
}

function clearPublicLeaderboardSearch() {
    _lbClearCache();
    const tidEl    = document.getElementById('pub-lb-tid');
    const teamEl   = document.getElementById('pub-lb-teamid');
    const resultEl = document.getElementById('pub-lb-result');
    const btnEl    = document.getElementById('pub-lb-view-btn');
    if (tidEl)    tidEl.value = '';
    if (teamEl)   teamEl.value = '';
    if (resultEl) resultEl.innerHTML = '';
    if (btnEl)    { btnEl.disabled = false; btnEl.textContent = '👑 View Leaderboard'; }
    // Hide clear btn, show inputs
    const clearBtn  = document.getElementById('pub-lb-clear-btn');
    const inputWrap = document.getElementById('pub-lb-inputs');
    if (clearBtn)  clearBtn.style.display = 'none';
    if (inputWrap) inputWrap.style.display = 'block';
}

function openPublicLeaderboard() {
    const modal = document.getElementById('pub-lb-modal');
    if (!modal) return;

    // Check session cache
    const cached = _lbLoadCache();
    if (cached) {
        // Pre-fill inputs silently then auto-load
        const tidEl  = document.getElementById('pub-lb-tid');
        const teamEl = document.getElementById('pub-lb-teamid');
        if (tidEl)  tidEl.value  = cached.tid;
        if (teamEl) teamEl.value = cached.teamId;
        modal.style.display = 'block';
        document.body.style.overflow = 'hidden';
        viewPublicLeaderboard();
        return;
    }

    // No cache — show blank form
    const tidEl    = document.getElementById('pub-lb-tid');
    const teamEl   = document.getElementById('pub-lb-teamid');
    const resultEl = document.getElementById('pub-lb-result');
    const btnEl    = document.getElementById('pub-lb-view-btn');
    const clearBtn  = document.getElementById('pub-lb-clear-btn');
    const inputWrap = document.getElementById('pub-lb-inputs');
    if (tidEl)    tidEl.value = '';
    if (teamEl)   teamEl.value = '';
    if (resultEl) resultEl.innerHTML = '';
    if (btnEl)    { btnEl.disabled = false; btnEl.textContent = '👑 View Leaderboard'; }
    if (clearBtn)  clearBtn.style.display = 'none';
    if (inputWrap) inputWrap.style.display = 'block';
    modal.style.display = 'block';
    document.body.style.overflow = 'hidden';
}

function closePublicLeaderboard() {
    const modal = document.getElementById('pub-lb-modal');
    if (!modal) return;
    modal.style.display = 'none';
    document.body.style.overflow = '';
}

async function viewPublicLeaderboard() {
    const tidInput  = (document.getElementById('pub-lb-tid')?.value     || '').trim();
    const teamInput = (document.getElementById('pub-lb-teamid')?.value   || '').trim();
    const resultDiv = document.getElementById('pub-lb-result');
    const btn       = document.getElementById('pub-lb-view-btn');
    const clearBtn  = document.getElementById('pub-lb-clear-btn');
    const inputWrap = document.getElementById('pub-lb-inputs');

    if (!tidInput || !teamInput) {
        if (resultDiv) resultDiv.innerHTML = `
        <div style="text-align:center;padding:20px;background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.25);border-radius:12px;">
            <p style="color:#f87171;font-size:13px;font-weight:700;margin:0;">⚠️ Please enter both Tournament ID and Team ID.</p>
        </div>`;
        return;
    }

    if (btn) { btn.disabled = true; btn.textContent = '⏳ Loading...'; }
    if (resultDiv) resultDiv.innerHTML = `<div style="text-align:center;padding:30px;color:#64748b;font-size:13px;">⏳ Verifying &amp; loading leaderboard...</div>`;

    try {
        const uid   = window._tgUser?.id || '';
        const uidQs = uid ? `?user_id=${encodeURIComponent(uid)}` : '';
        const res   = await fetchWithRetry(
            `${CONFIG.API_BASE_URL}/tournament/${encodeURIComponent(tidInput)}/leaderboard${uidQs}`
        );
        const data  = await res.json();

        // Invalid tournament or API error
        if (!res.ok || (data.status && data.status !== 'success' && data.status !== 'not_joined')) {
            if (resultDiv) resultDiv.innerHTML = `
            <div style="text-align:center;padding:28px 20px;background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.25);border-radius:14px;">
                <span style="font-size:38px;display:block;margin-bottom:10px;">❌</span>
                <p style="color:#f87171;font-size:14px;font-weight:800;margin:0 0 4px;">Invalid Tournament ID or Team ID</p>
                <p style="color:#64748b;font-size:12px;margin:0;">Check the IDs and try again.</p>
            </div>`;
            return;
        }

        const board = data.leaderboard || [];

        // Leaderboard not available yet (empty results)
        if (board.length === 0) {
            if (resultDiv) resultDiv.innerHTML = `
            <div style="text-align:center;padding:32px 20px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:14px;">
                <span style="font-size:38px;display:block;margin-bottom:10px;">⏳</span>
                <p style="color:#e2e8f0;font-size:14px;font-weight:700;margin:0 0 6px;">Leaderboard not available yet.</p>
                <p style="color:#64748b;font-size:12px;margin:0;">Results will appear after rounds are submitted by admin.</p>
            </div>`;
            return;
        }

        // Verify Team ID exists in leaderboard
        const searchTeamId = teamInput.toLowerCase();
        const teamExists   = board.some(r => (r.team_id || '').toLowerCase() === searchTeamId);
        if (!teamExists) {
            if (resultDiv) resultDiv.innerHTML = `
            <div style="text-align:center;padding:28px 20px;background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.25);border-radius:14px;">
                <span style="font-size:38px;display:block;margin-bottom:10px;">❌</span>
                <p style="color:#f87171;font-size:14px;font-weight:800;margin:0 0 4px;">Invalid Tournament ID or Team ID</p>
                <p style="color:#64748b;font-size:12px;margin:0;">This Team ID was not found in the tournament.</p>
            </div>`;
            return;
        }

        // ── Save to session cache (10-min auto-restore) ──────────────
        _lbSaveCache(tidInput, teamInput);
        // Show Clear Search button, hide input form
        if (clearBtn)  { clearBtn.style.display = 'flex'; }
        if (inputWrap) { inputWrap.style.display = 'none'; }

        // ── Build leaderboard HTML ──────────────────────────────────────────
        const totalRounds = data.total_rounds || 1;
        const rankEmoji   = { 1: '🥇', 2: '🥈', 3: '🥉' };
        const lbTitle = data.is_completed
            ? '🏆 Final Leaderboard'
            : data.is_live
                ? `🔴 Live Leaderboard · Round ${data.current_round || 0}/${totalRounds}`
                : `📊 Tournament Leaderboard · ${data.current_round || 0}/${totalRounds} rounds played`;

        let html = '';

        // Status banner
        if (data.is_completed) {
            html += `
            <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;
                        background:rgba(241,196,15,0.08);border:1px solid rgba(241,196,15,0.25);
                        border-radius:12px;margin-bottom:12px;">
                <span style="font-size:20px;">🏆</span>
                <div>
                    <p style="font-size:13px;font-weight:800;color:#f1c40f;margin:0;">Tournament Completed</p>
                    <p style="font-size:11px;color:#64748b;margin:2px 0 0;">Final standings — official results.</p>
                </div>
            </div>`;
        } else if (data.is_live) {
            html += `
            <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;
                        background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.28);
                        border-radius:12px;margin-bottom:12px;">
                <span style="font-size:20px;">🔴</span>
                <div>
                    <p style="font-size:13px;font-weight:800;color:#f87171;margin:0;">Match is Live!</p>
                    <p style="font-size:11px;color:#64748b;margin:2px 0 0;">Scores update after each round.</p>
                </div>
            </div>`;
        }

        html += `<p style="font-size:11px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin:0 0 10px;">${lbTitle}</p>`;

        // ── Top 3 — special winner cards ──
        const top3 = board.filter(r => r.position <= 3).sort((a, b) => a.position - b.position);
        const rest = board.filter(r => r.position > 3);

        top3.forEach(row => {
            const pos       = row.position;
            const isMyTeam  = (row.team_id || '').toLowerCase() === searchTeamId;
            const cardCls   = pos === 1 ? 'rank-1' : pos === 2 ? 'rank-2' : 'rank-3';
            const nameClr   = pos === 1 ? '#f1c40f' : pos === 2 ? '#94a3b8' : '#cd7f32';
            const myStyle   = isMyTeam
                ? 'box-shadow:0 0 0 2px #a78bfa,0 0 18px rgba(139,92,246,0.28);'
                : '';
            const booyahCnt = row.total_booyah || 0;
            const booyahBadge = booyahCnt > 0
                ? `<span style="display:inline-flex;align-items:center;gap:2px;
                       background:linear-gradient(135deg,#f1c40f,#f39c12);
                       border-radius:6px;padding:1px 7px;font-size:11px;font-weight:900;
                       color:#1a1200;letter-spacing:0.3px;margin-left:6px;flex-shrink:0;">
                       B!${booyahCnt > 1 ? ' ×' + booyahCnt : ''}
                   </span>`
                : '';
            html += `
            <div class="t-winner-card ${cardCls}" style="${myStyle}">
                <span style="font-size:30px;flex-shrink:0;">${rankEmoji[pos]}</span>
                <div style="flex:1;min-width:0;">
                    <div style="display:flex;align-items:center;gap:0;flex-wrap:nowrap;">
                        <p style="font-size:14px;font-weight:800;color:${nameClr};margin:0;
                                   white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                            ${_esc(row.team_name || row.team_id)}
                        </p>
                        ${booyahBadge}
                    </div>
                    <p style="font-size:11px;color:#64748b;margin:2px 0 0;">${_esc(row.team_id)}</p>
                    ${isMyTeam ? `<span style="display:inline-flex;align-items:center;gap:3px;
                        background:rgba(139,92,246,0.18);border:1px solid rgba(139,92,246,0.35);
                        border-radius:20px;padding:2px 9px;font-size:10px;font-weight:800;
                        color:#a78bfa;margin-top:5px;">⭐ Your Team</span>` : ''}
                </div>
                <div style="text-align:right;flex-shrink:0;">
                    <p style="font-size:20px;font-weight:900;color:#f1c40f;margin:0;">${row.total_points ?? 0}</p>
                    <p style="font-size:10px;color:#64748b;margin:2px 0 0;">pts</p>
                    <p style="font-size:10px;color:#94a3b8;margin:1px 0 0;">${row.total_kills ?? 0} kills</p>
                </div>
            </div>`;
        });

        // ── Rest of teams — compact rows ──
        if (rest.length > 0) {
            let roundCols = '';
            for (let rn = 1; rn <= totalRounds; rn++) {
                roundCols += `<span style="flex:0.8;text-align:center;font-size:10px;color:#64748b;font-weight:700;">R${rn}</span>`;
            }
            html += `
            <div style="display:flex;align-items:center;gap:6px;padding:4px 8px;margin:10px 0 4px;">
                <span style="width:28px;"></span>
                <span style="flex:2;font-size:10px;color:#64748b;font-weight:700;">TEAM</span>
                ${roundCols}
                <span style="flex:1;text-align:center;font-size:10px;color:#64748b;font-weight:700;">KILLS</span>
                <span style="flex:1;text-align:center;font-size:10px;color:#f1c40f;font-weight:700;">PTS</span>
            </div>`;

            rest.forEach(row => {
                const pos       = row.position || '?';
                const isMyTeam  = (row.team_id || '').toLowerCase() === searchTeamId;
                const booyahCnt = row.total_booyah || 0;
                const booyahBadge = booyahCnt > 0
                    ? `<span style="display:inline-flex;align-items:center;
                           background:linear-gradient(135deg,#f1c40f,#f39c12);
                           border-radius:5px;padding:0px 5px;font-size:9px;font-weight:900;
                           color:#1a1200;margin-left:4px;flex-shrink:0;">
                           B!${booyahCnt > 1 ? ' ×' + booyahCnt : ''}
                       </span>`
                    : '';
                let roundPts = '';
                for (let rn = 1; rn <= totalRounds; rn++) {
                    const pts = row.rounds?.[rn] ?? '—';
                    roundPts += `<span style="flex:0.8;text-align:center;font-size:11px;color:${typeof pts === 'number' ? '#e2e8f0' : '#475569'};">${pts}</span>`;
                }
                html += `
                <div style="display:flex;align-items:center;gap:6px;padding:8px;
                             background:${isMyTeam ? 'rgba(139,92,246,0.10)' : 'rgba(255,255,255,0.025)'};
                             border:1px solid ${isMyTeam ? 'rgba(139,92,246,0.35)' : 'rgba(255,255,255,0.06)'};
                             border-radius:10px;margin-bottom:5px;
                             ${isMyTeam ? 'box-shadow:0 0 10px rgba(139,92,246,0.18);' : ''}">
                    <span style="width:28px;font-size:12px;text-align:center;color:#64748b;font-weight:700;">#${_esc(String(pos))}</span>
                    <div style="flex:2;min-width:0;">
                        <div style="display:flex;align-items:center;flex-wrap:nowrap;">
                            <p style="font-size:12px;font-weight:800;
                                       color:${isMyTeam ? '#a78bfa' : '#e2e8f0'};
                                       margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                                ${_esc(row.team_name || row.team_id)}
                            </p>
                            ${booyahBadge}
                        </div>
                        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                            <p style="font-size:10px;color:#64748b;margin:1px 0 0;">${_esc(row.team_id)}</p>
                            ${isMyTeam ? `<span style="display:inline-flex;align-items:center;gap:3px;
                                background:rgba(139,92,246,0.18);border:1px solid rgba(139,92,246,0.35);
                                border-radius:20px;padding:1px 7px;font-size:10px;font-weight:800;
                                color:#a78bfa;">⭐ Your Team</span>` : ''}
                        </div>
                    </div>
                    ${roundPts}
                    <span style="flex:1;text-align:center;font-size:12px;color:#94a3b8;font-weight:700;">${row.total_kills ?? 0}</span>
                    <span style="flex:1;text-align:center;font-size:13px;color:#f1c40f;font-weight:900;">${row.total_points ?? 0}</span>
                </div>`;
            });
        }

        if (resultDiv) resultDiv.innerHTML = html;

    } catch (_err) {
        if (resultDiv) resultDiv.innerHTML = `
        <div style="text-align:center;padding:28px 20px;background:rgba(239,68,68,0.07);
                    border:1px solid rgba(239,68,68,0.25);border-radius:14px;">
            <span style="font-size:38px;display:block;margin-bottom:10px;">❌</span>
            <p style="color:#f87171;font-size:14px;font-weight:800;margin:0 0 4px;">Invalid Tournament ID or Team ID</p>
            <p style="color:#64748b;font-size:12px;margin:0;">Could not load leaderboard. Check IDs and try again.</p>
        </div>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '👑 View Leaderboard'; }
    }
}

// ============================================================
// TOURNAMENT TERMS & CONDITIONS POPUP
// ============================================================
function _showTournamentTnC(onAccept) {
    // Remove any existing instance
    const existing = document.getElementById('_tnc-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = '_tnc-overlay';
    overlay.style.cssText = [
        'position:fixed', 'inset:0', 'z-index:99999',
        'background:rgba(0,0,0,0.82)', 'backdrop-filter:blur(6px)',
        'display:flex', 'align-items:center', 'justify-content:center',
        'padding:16px', 'box-sizing:border-box'
    ].join(';');

    overlay.innerHTML = `
        <div style="
            background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);
            border:1px solid rgba(241,196,15,0.30);
            border-radius:20px; padding:22px 20px 20px;
            max-width:360px; width:100%; max-height:88vh;
            overflow-y:auto; box-shadow:0 20px 60px rgba(0,0,0,0.7);
            font-family:inherit;
        ">
            <h3 style="margin:0 0 4px;font-size:17px;color:#f1c40f;text-align:center;">
                🏆 Tournament Terms & Conditions
            </h3>
            <p style="margin:0 0 14px;font-size:11px;color:#64748b;text-align:center;">
                Please read carefully before joining
            </p>

            <div style="
                background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07);
                border-radius:12px; padding:14px; margin-bottom:16px;
                font-size:12.5px; color:#94a3b8; line-height:1.7;
            ">
                <p style="margin:0 0 8px;color:#cbd5e1;font-weight:700;">📋 General Rules</p>
                <p style="margin:0 0 6px;">• Entry fee is <b style="color:#f87171;">non-refundable</b> once registration is confirmed.</p>
                <p style="margin:0 0 6px;">• Players must use their own Free Fire UID & Nickname. Fake details will lead to <b>disqualification</b>.</p>
                <p style="margin:0 0 6px;">• Cheating, hacking, or using any unfair means is strictly prohibited. Violators will be permanently banned.</p>
                <p style="margin:0 0 6px;">• You must join the in-game room on time. Late entries will not be accommodated.</p>
                <p style="margin:0 0 6px;">• Results declared by the organizer are <b>final</b> and cannot be disputed.</p>

                <p style="margin:10px 0 8px;color:#cbd5e1;font-weight:700;">💰 Prize & Payout</p>
                <p style="margin:0 0 6px;">• Prize is credited to your in-app Rupee Wallet within 24 hours of result declaration.</p>
                <p style="margin:0 0 6px;">• Daksh Grand Earn reserves the right to disqualify any suspicious account without prior notice.</p>

                <p style="margin:10px 0 8px;color:#cbd5e1;font-weight:700;">⚠️ Disclaimer</p>
                <p style="margin:0;">• By registering, you confirm that you are eligible to participate and you accept all rules without exception.</p>
            </div>

            <label style="display:flex;align-items:center;gap:10px;cursor:pointer;margin-bottom:16px;user-select:none;">
                <input type="checkbox" id="_tnc-checkbox" style="
                    width:18px;height:18px;accent-color:#f1c40f;cursor:pointer;flex-shrink:0;
                ">
                <span style="font-size:13px;color:#e2e8f0;line-height:1.4;">
                    I have read and agree to the <b style="color:#f1c40f;">Terms & Conditions</b>
                </span>
            </label>

            <div style="display:flex;gap:10px;">
                <button id="_tnc-cancel" style="
                    flex:1; background:rgba(255,255,255,0.06);
                    border:1px solid rgba(255,255,255,0.10);
                    color:#94a3b8; border-radius:12px;
                    padding:12px; font-size:13px; font-weight:700;
                    cursor:pointer;
                ">✖ Cancel</button>
                <button id="_tnc-proceed" style="
                    flex:2; background:linear-gradient(135deg,#d4a017,#f1c40f);
                    border:none; color:#0f172a; border-radius:12px;
                    padding:12px; font-size:13px; font-weight:800;
                    cursor:pointer; opacity:0.45; transition:opacity 0.2s;
                " disabled>✅ Proceed</button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    const checkbox = overlay.querySelector('#_tnc-checkbox');
    const proceedBtn = overlay.querySelector('#_tnc-proceed');
    const cancelBtn  = overlay.querySelector('#_tnc-cancel');

    // Enable Proceed only when checkbox is ticked
    checkbox.addEventListener('change', () => {
        proceedBtn.disabled = !checkbox.checked;
        proceedBtn.style.opacity = checkbox.checked ? '1' : '0.45';
    });

    cancelBtn.addEventListener('click', () => overlay.remove());

    proceedBtn.addEventListener('click', () => {
        if (!checkbox.checked) return;
        _tournamentTncAccepted = true;   // remember for this session
        overlay.remove();
        onAccept();                      // run the original join logic
    });

    // Tap outside to dismiss
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
}

async function registerForTournament() {
    // Show T&C popup first — on accept it re-calls this function (with flag set)
    if (!_tournamentTncAccepted) {
        _showTournamentTnC(() => registerForTournament());
        return;
    }

    const btn     = document.getElementById('t-reg-btn');
    const msg     = document.getElementById('t-reg-msg');
    // Detect mode from which form is rendered
    const _hasTeamForm = !!document.getElementById('t-team-name');
    const _hasDuoP2    = !!document.getElementById('t-m2-uid') && !document.getElementById('t-m3-uid');
    const isTeamForm   = _hasTeamForm;
    const isDuoForm    = _hasTeamForm && _hasDuoP2;

    if (!_selectedTid) {
        if (msg) { msg.style.color = '#ef4444'; msg.textContent = '⚠️ No tournament selected.'; }
        return;
    }
    if (!userId) {
        if (msg) { msg.style.color = '#ef4444'; msg.textContent = '⚠️ User not identified.'; }
        return;
    }

    let payload;

    if (isTeamForm) {
        // ── Duo / Squad validation ──
        const teamName = (document.getElementById('t-team-name')?.value || '').trim();
        if (!teamName) {
            if (msg) { msg.style.color = '#ef4444'; msg.textContent = '⚠️ Team name required.'; }
            return;
        }

        const maxSlots = isDuoForm ? 2 : 4;
        const members  = [];
        for (let i = 1; i <= maxSlots; i++) {
            const mUid  = (document.getElementById(`t-m${i}-uid`)?.value  || '').trim();
            const mNick = (document.getElementById(`t-m${i}-nick`)?.value || '').trim();
            // For squad: M3/M4 are optional — skip if both empty
            if (!isDuoForm && i > 2 && !mUid && !mNick) continue;
            if (!mUid || !mNick) {
                const label = isDuoForm ? `Player ${i}` : `Member ${i}`;
                if (msg) { msg.style.color = '#ef4444'; msg.textContent = `⚠️ ${label}: FF UID and FF Name are both required.`; }
                return;
            }
            if (!/^\d{5,15}$/.test(mUid)) {
                const label = isDuoForm ? `Player ${i}` : `Member ${i}`;
                if (msg) { msg.style.color = '#ef4444'; msg.textContent = `⚠️ ${label}: FF UID must be 5–15 digits only.`; }
                return;
            }
            members.push({ ff_uid: mUid, ff_nickname: mNick });
        }
        const minNeeded = isDuoForm ? 2 : 2;
        if (members.length < minNeeded) {
            if (msg) { msg.style.color = '#ef4444'; msg.textContent = `⚠️ Minimum ${minNeeded} players required.`; }
            return;
        }

        payload = { user_id: userId, tournament_id: _selectedTid, team_name: teamName, members };

    } else {
        // ── Solo validation ──
        const uid  = (document.getElementById('t-ff-uid')?.value      || '').trim();
        const nick = (document.getElementById('t-ff-nickname')?.value  || '').trim();
        if (!uid || !nick) {
            if (msg) { msg.style.color = '#ef4444'; msg.textContent = '⚠️ FF UID and FF Name are both required.'; }
            return;
        }
        if (!/^\d{5,15}$/.test(uid)) {
            if (msg) { msg.style.color = '#ef4444'; msg.textContent = '⚠️ FF UID must be 5-15 digits only.'; }
            return;
        }
        payload = { user_id: userId, tournament_id: _selectedTid, ff_uid: uid, ff_nickname: nick };
    }

    const loadingTxt = isDuoForm ? '⏳ Registering Duo...' : isTeamForm ? '⏳ Registering Squad...' : '⏳ Registering...';
    const resetTxt   = isDuoForm ? '🤝 Register Duo'       : isTeamForm ? '🛡️ Register Squad'       : '🎯 Register for Tournament';

    if (btn) { btn.disabled = true; btn.textContent = loadingTxt; }
    if (msg) { msg.style.color = '#94a3b8'; msg.textContent = '⏳ Submitting registration...'; }

    try {
        const res  = await fetchWithRetry(CONFIG.API_BASE_URL + '/tournament/register', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(payload),
        });
        const data = await res.json();

        if (data.status === 'success') {
            if (msg) { msg.style.color = '#4ade80'; msg.textContent = data.message || '🎉 Registered!'; }
            delete _tournamentCache[_selectedTid];
            delete _tournamentRegCache[_selectedTid];
            setTimeout(() => loadTournamentById(_selectedTid, true), 800);
        } else {
            if (msg) { msg.style.color = '#ef4444'; msg.textContent = '❌ ' + (data.message || 'Registration failed.'); }
            if (btn) { btn.disabled = false; btn.textContent = resetTxt; }
        }
    } catch (e) {
        if (msg) { msg.style.color = '#ef4444'; msg.textContent = '⚠️ Network error. Please try again.'; }
        if (btn) { btn.disabled = false; btn.textContent = resetTxt; }
    }
}

function _esc(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// Load tournament indicator on startup (silently — no modal open)
async function _initTournamentIndicator() {
    try {
        const res  = await fetchWithRetry(CONFIG.API_BASE_URL + '/tournament');
        const data = await res.json();

        if (data.status !== 'success') return;
        _allTournaments = data.tournaments || [];

        const hasActive = _allTournaments.some(t => ['registration_open','match_live'].includes(t.status));
        const dot  = document.getElementById('t-trophy-dot');
        const tBtn = document.getElementById('tournament-trophy-btn');
        if (dot) dot.style.display = hasActive ? 'block' : 'none';
        if (tBtn) tBtn.classList.toggle('has-active', hasActive);

        // Pre-select first tournament for faster modal open
        if (_allTournaments.length > 0 && !_selectedTid) {
            _selectedTid = _allTournaments[0].tournament_id;
        }
    } catch(_) { /* silent fail */ }
}

// ============================================================
// APP INIT
// ============================================================
window.addEventListener('DOMContentLoaded', () => {
    // Buttons in this page are actions, not form submits. Explicitly setting
    // the type also protects future HTML changes from accidental reloads.
    document.querySelectorAll('button:not([type])').forEach(button => {
        button.type = 'button';
    });

    const adminEl = document.getElementById('admin-tg-username');
    if (adminEl && CONFIG.ADMIN_TELEGRAM) {
        const u = String(CONFIG.ADMIN_TELEGRAM);
        adminEl.textContent = u.startsWith('@') ? u : '@' + u;
    }

    // Draw spin wheel immediately so it shows on load
    drawSpinWheel(_wheelRot);

    renderSponsorSlots({}, [], {});
    fetchLiveData();
    checkDevice();
    preloadMonetagAd();
    applyReferralLock();

    setInterval(fetchLiveData,      300000);  // data refresh every 5 min
    setInterval(refreshLeaderboard, 600000);  // leaderboard refresh every 10 min

    // Tournament: silently load status + show dot indicator
    setTimeout(_initTournamentIndicator, 2000);

    // BUG FIX: Refresh ke baad last active tab restore karo
    try {
        const savedTab = sessionStorage.getItem('activeTab');
        if (savedTab && savedTab !== 'rewards' && document.getElementById(savedTab)) {
            const navItems = document.querySelectorAll('.nav-item');
            const tabOrder = ['rewards', 'tasks', 'leaderboard', 'refer', 'profile'];
            const tabIdx = tabOrder.indexOf(savedTab);
            switchTab(savedTab, tabIdx >= 0 ? navItems[tabIdx] : null);
        }
    } catch(_) {}
});

// ============================================================
// PREMIUM MEMBERSHIP — Modal & Buy Flow
// ============================================================

let _selectedPlan = null;

const PREMIUM_PLANS_INFO = {
    weekly:    { label: 'Weekly',    days: 7,   price: 29,  perDay: '~₹4/day' },
    monthly:   { label: 'Monthly',   days: 30,  price: 79,  perDay: '~₹2.6/day' },
    quarterly: { label: 'Quarterly', days: 90,  price: 199, perDay: '~₹2.2/day' },
};

function showPremiumModal() {
    document.getElementById('premium-modal-overlay').style.display = 'block';
    document.getElementById('premium-modal').style.display = 'block';
    document.body.style.overflow = 'hidden';
    resetPremiumModal();
}

function hidePremiumModal() {
    document.getElementById('premium-modal-overlay').style.display = 'none';
    document.getElementById('premium-modal').style.display = 'none';
    document.body.style.overflow = '';
}

function resetPremiumModal() {
    _selectedPlan = null;
    document.getElementById('prem-plan-section').style.display = 'block';
    document.getElementById('prem-pay-section').style.display  = 'none';
    // Reset plan card borders
    ['weekly','monthly','quarterly'].forEach(p => {
        const el = document.getElementById('plan-' + p);
        if (!el) return;
        el.style.border = p === 'monthly'
            ? '2px solid #f1c40f'
            : '2px solid rgba(241,196,15,0.2)';
        el.style.background = p === 'monthly'
            ? 'rgba(241,196,15,0.08)'
            : 'rgba(241,196,15,0.04)';
    });
    // Clear transaction ID input
    const txnInput = document.getElementById('prem-txn-input');
    const txnTick  = document.getElementById('prem-txn-tick');
    const txnErr   = document.getElementById('prem-txn-err');
    if (txnInput) { txnInput.value = ''; txnInput.style.borderColor = 'rgba(255,255,255,0.1)'; }
    if (txnTick)  txnTick.style.display  = 'none';
    if (txnErr)   txnErr.style.display   = 'none';
}

function selectPlan(plan) {
    _selectedPlan = plan;
    const info    = PREMIUM_PLANS_INFO[plan];
    if (!info) return;

    // Highlight selected card
    ['weekly','monthly','quarterly'].forEach(p => {
        const el = document.getElementById('plan-' + p);
        if (!el) return;
        const selected = p === plan;
        el.style.border      = selected ? '2px solid #4ade80' : '2px solid rgba(241,196,15,0.15)';
        el.style.background  = selected ? 'rgba(34,197,94,0.08)' : 'rgba(241,196,15,0.03)';
    });

    // UPI ID — from CONFIG.ADMIN_UPI, fallback to hardcoded default
    const adminUpi = (typeof CONFIG !== 'undefined' && CONFIG.ADMIN_UPI)
        ? CONFIG.ADMIN_UPI
        : 'sahdaksh@fam';

    // QR Image — CONFIG.ADMIN_QR_URL > absolute URL built from page location > hidden
    let qrUrl = '';
    if (typeof CONFIG !== 'undefined' && CONFIG.ADMIN_QR_URL) {
        qrUrl = CONFIG.ADMIN_QR_URL;
    } else {
        // Build absolute URL from current page so relative paths always work
        const base = window.location.href.replace(/[^/]*$/, '');
        qrUrl = base + 'payment_qr.jpg';
    }

    // Show QR image
    const qrWrap = document.getElementById('prem-qr-wrap');
    const qrImg  = document.getElementById('prem-qr-img');
    if (qrImg && qrWrap) {
        qrImg.src        = qrUrl;
        qrImg.onerror    = () => { qrWrap.style.display = 'none'; };
        qrImg.onload     = () => { qrWrap.style.display = 'block'; };
        qrWrap.style.display = 'block';
    }

    // Update payment section
    const upiEl    = document.getElementById('prem-upi-display');
    const amountEl = document.getElementById('prem-amount-display');
    if (upiEl)    upiEl.textContent    = adminUpi;
    if (amountEl) amountEl.textContent = `Amount: ₹${info.price} (${info.label} — ${info.days} days)`;

    // Show payment section, hide plan section
    document.getElementById('prem-plan-section').style.display = 'none';
    document.getElementById('prem-pay-section').style.display  = 'block';

    // Scroll to top of modal
    const modal = document.getElementById('premium-modal');
    if (modal) modal.scrollTop = 0;
}

function copyUpi() {
    const adminUpi = (typeof CONFIG !== 'undefined' && CONFIG.ADMIN_UPI)
        ? CONFIG.ADMIN_UPI
        : 'sahdaksh@fam';
    if (!adminUpi) {
        showToast('⚠️ UPI ID not configured. Contact admin.', 'error');
        return;
    }
    copyText(adminUpi, 'UPI ID copied!');
}

function validateTxnInput() {
    const inp  = document.getElementById('prem-txn-input');
    const tick = document.getElementById('prem-txn-tick');
    const err  = document.getElementById('prem-txn-err');
    if (!inp) return;
    const val = inp.value.trim();
    const valid = val.length >= 6;
    // Border colour feedback
    inp.style.borderColor = val.length === 0
        ? 'rgba(255,255,255,0.1)'
        : valid ? '#4ade80' : '#ef4444';
    // Tick icon
    if (tick) tick.style.display = valid ? 'inline' : 'none';
    // Hide error when user starts typing valid input
    if (err && valid) err.style.display = 'none';
}

function openBotForPayment() {
    if (!_selectedPlan) return;

    // Validate transaction ID
    const txnInput = document.getElementById('prem-txn-input');
    const txnErr   = document.getElementById('prem-txn-err');
    const txnId    = txnInput ? txnInput.value.trim() : '';
    if (txnId.length < 6) {
        if (txnErr)   txnErr.style.display = 'block';
        if (txnInput) {
            txnInput.style.borderColor = '#ef4444';
            txnInput.focus();
        }
        return;
    }

    const info  = PREMIUM_PLANS_INFO[_selectedPlan];
    const botUN = (typeof CONFIG !== 'undefined' && CONFIG.BOT_USERNAME) ? CONFIG.BOT_USERNAME : '';
    if (!botUN) {
        showToast('⚠️ Bot username not configured.', 'error');
        return;
    }
    const uid    = userId || 'unknown';
    const botUrl = `https://t.me/${botUN.replace('@','')}?start=premium_pay_${_selectedPlan}_${uid}_${encodeURIComponent(txnId)}`;
    hidePremiumModal();
    openExternalLink(botUrl);
    showToast('📤 Bot opened — send your screenshot!', 'ok');
}

// ============================================================
// 💎 PREMIUM CARD CLICK — modal sirf tab khule jab premium nahi ho
// ============================================================
function handlePremiumCardClick() {
    const isPrem = !!(userData && userData.premium_info && userData.premium_info.premium);
    if (isPrem) {
        // Already premium — buy modal mat kholo, sirf info toast dikhao
        const info = userData.premium_info || {};
        showToast(`✅ Premium Active · ${info.plan || 'Standard'} · ${info.days_left || 0} days left`, 'success');
        return;
    }
    showPremiumModal();
}

// Update premium card on home tab based on user's premium status
function updatePremiumCard(premInfo) {
    const card      = document.getElementById('premium-buy-card');
    const title     = document.getElementById('prem-card-title');
    const sub       = document.getElementById('prem-card-sub');
    const btnLabel  = document.getElementById('prem-card-btn-label');
    if (!card) return;

    if (premInfo && premInfo.premium) {
        // Already premium — show status with blue tick
        if (title) {
            title.innerHTML = `
                <svg width="15" height="15" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg" style="vertical-align:middle;margin-right:4px;margin-bottom:1px;">
                    <circle cx="9" cy="9" r="9" fill="#1D9BF0"/>
                    <path d="M5 9.5L7.5 12L13 6.5" stroke="white" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>Premium Active`;
            title.style.color = '#1D9BF0';
        }
        if (sub)      sub.textContent     = `${premInfo.plan || 'Standard'} · ${premInfo.days_left || 0} days remaining`;
        if (btnLabel) btnLabel.textContent = 'View Benefits →';
        card.style.borderColor = 'rgba(29,155,240,0.5)';
        card.style.boxShadow   = '0 4px 20px rgba(29,155,240,0.08)';
    } else {
        if (title)    title.textContent   = 'Get Premium';
        if (sub)      sub.textContent     = '2x Coins • 15 Spins/Day • Withdraw from 10k';
        if (btnLabel) btnLabel.textContent = 'From ₹29 →';
        card.style.borderColor = 'rgba(241,196,15,0.35)';
    }
}
