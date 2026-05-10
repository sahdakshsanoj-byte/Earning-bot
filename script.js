// ============================================================
// SCRIPT.JS — Daksh Grand Earn (Secure & Clean)
// ============================================================

const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();
tg.enableClosingConfirmation();

const userId = tg.initDataUnsafe?.user?.id
    || new URLSearchParams(window.location.search).get('user_id');

window.USER_ID = userId; // expose for inline scripts

let userData = {};

// Tracks in-flight requests to prevent duplicate clicks
const _pendingRequests = new Set();
let monetagSdkPromise = null;
let monetagPreloaded = false;

// ============================================================
// CONSTANTS (must match backend)
// ============================================================
const MAX_ADS_PER_DAY    = 10;
const MAX_YT_PER_DAY     = 3;
const MAX_WEB_PER_DAY    = 3;
const MIN_WITHDRAW_COINS = 5000;
const ALL_TASKS_BONUS    = 10;

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
        script.dataset.sdk = `show_${zoneId}`;
        script.onload = () => getMonetagShowFunction()
            ? resolve()
            : reject(new Error("Monetag show function not found"));
        script.onerror = () => reject(new Error("Monetag SDK failed to load"));
        document.head.appendChild(script);
    }).catch((err) => {
        monetagSdkPromise = null;
        throw err;
    });

    return monetagSdkPromise;
}

async function preloadMonetagAd() {
    if (!userId || !getMonetagZoneId()) return;
    try {
        await loadMonetagSdk();
        const showMonetagAd = getMonetagShowFunction();
        if (!showMonetagAd) return;
        await showMonetagAd({
            type: 'preload',
            timeout: 5,
            ymid: String(userId),
            requestVar: 'ad_reward',
        });
        monetagPreloaded = true;
    } catch (e) {
        monetagPreloaded = false;
    }
}

// ============================================================
// TOAST NOTIFICATION
// ============================================================
function showToast(msg, type = "info") {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.className = `show ${type}`;
    setTimeout(() => { toast.className = ''; }, 3500);
}

// ============================================================
// FETCH WITH RETRY — 3 retries, 10s timeout
// ============================================================
async function fetchWithRetry(url, options = {}, retries = 3, delayMs = 2000) {
    for (let attempt = 1; attempt <= retries; attempt++) {
        try {
            const controller = new AbortController();
            const timeout    = setTimeout(() => controller.abort(), 10000);
            const res        = await fetch(url, { ...options, signal: controller.signal });
            clearTimeout(timeout);
            if (!res.ok && res.status >= 400 && res.status < 500) return res;
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return res;
        } catch (err) {
            if (attempt === retries) throw err;
            await new Promise(r => setTimeout(r, delayMs));
        }
    }
}

// ============================================================
// COUNTDOWN HELPER — generic, calls updateFn(s) every tick
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
// DAILY BONUS 24-HOUR LIVE COUNTDOWN
// ============================================================
let _dailyCountdownInterval = null;

/**
 * Parse an ISO timestamp from server (may or may not have Z suffix).
 * Always treat as UTC.
 */
function parseUTCTimestamp(ts) {
    if (!ts) return null;
    try {
        // Append Z if missing so browser treats it as UTC, not local time
        const str = ts.includes('Z') || ts.includes('+') ? ts : ts + 'Z';
        const d = new Date(str);
        return isNaN(d.getTime()) ? null : d;
    } catch (e) { return null; }
}

/**
 * Start a live HH:MM:SS countdown on the daily bonus button.
 * @param {number} remainingSeconds - seconds remaining until next claim
 */
function startDailyCountdown(remainingSeconds) {
    if (_dailyCountdownInterval) clearInterval(_dailyCountdownInterval);

    const btn      = document.getElementById('daily-btn');
    const timerEl  = document.getElementById('daily-timer');
    const countEl  = document.getElementById('daily-countdown');

    let secs = Math.max(0, Math.floor(remainingSeconds));

    if (btn) {
        btn.disabled     = true;
        btn.style.opacity = '0.6';
        btn.innerText    = 'Come Back Later';
    }
    if (timerEl) timerEl.style.display = 'block';

    function fmt(n) { return String(n).padStart(2, '0'); }

    function tick() {
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = secs % 60;
        const display = `${fmt(h)}:${fmt(m)}:${fmt(s)}`;
        if (countEl) countEl.textContent = display;
        // Also reflect in button text so it's visible even if timer element is hidden
        if (btn) btn.innerText = `⏰ ${display} left`;
    }

    tick();

    _dailyCountdownInterval = setInterval(() => {
        secs--;
        if (secs <= 0) {
            clearInterval(_dailyCountdownInterval);
            _dailyCountdownInterval = null;
            if (timerEl) timerEl.style.display = 'none';
            if (btn) {
                btn.disabled      = false;
                btn.style.opacity = '1';
                btn.innerText     = 'Claim Now';
            }
            showToast('🎁 Daily bonus is ready! Claim your 10 coins!', 'success');
            return;
        }
        tick();
    }, 1000);
}

/**
 * Check lastClaimTs from server and start countdown if needed.
 * Called from fetchLiveData after getting user data.
 */
function checkDailyBonus(lastClaimTs) {
    const btn = document.getElementById('daily-btn');
    if (!btn) return;

    if (!lastClaimTs) {
        btn.disabled      = false;
        btn.style.opacity = '1';
        btn.innerText     = 'Claim Now';
        const timerEl = document.getElementById('daily-timer');
        if (timerEl) timerEl.style.display = 'none';
        if (_dailyCountdownInterval) clearInterval(_dailyCountdownInterval);
        return;
    }

    const lastDt = parseUTCTimestamp(lastClaimTs);
    if (!lastDt) {
        btn.disabled  = false;
        btn.innerText = 'Claim Now';
        return;
    }

    const diffMs  = Date.now() - lastDt.getTime();
    const diffSec = diffMs / 1000;
    const totalSec = 24 * 3600; // 24 hours in seconds

    if (diffSec < totalSec) {
        const remaining = Math.ceil(totalSec - diffSec);
        // Only restart countdown if not already running to avoid flicker
        if (!_dailyCountdownInterval) {
            startDailyCountdown(remaining);
        }
    } else {
        // Already past 24h — enable button
        if (_dailyCountdownInterval) {
            clearInterval(_dailyCountdownInterval);
            _dailyCountdownInterval = null;
        }
        btn.disabled      = false;
        btn.style.opacity = '1';
        btn.innerText     = 'Claim Now';
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

    try {
        await requireAdWatch();
    } catch (e) {
        showToast('📺 Watch the full ad to claim your daily bonus!', 'error');
        if (btn) { btn.disabled = false; btn.innerText = 'Claim Now'; }
        _pendingRequests.delete('claimDaily');
        return;
    }

    if (btn) btn.innerText = 'Claiming...';
    try {
        const res  = await fetchWithRetry(
            `${CONFIG.API_BASE_URL}/claim_daily/${userId}`,
            { method: 'POST' }
        );
        const data = await res.json();

        if (data.status === 'success') {
            showToast('🎁 10 coins added to your balance!', 'success');
            startDailyCountdown(24 * 3600);
            fetchLiveData();
        } else {
            showToast(data.message || 'Already claimed today.', 'error');
            const remSecs = data.data?.remaining_seconds;
            if (remSecs && remSecs > 0) {
                startDailyCountdown(remSecs);
            } else {
                if (btn) { btn.disabled = false; btn.innerText = 'Claim Now'; }
            }
        }
    } catch (e) {
        showToast('⚠️ Error! Please retry.', 'error');
        if (btn) { btn.disabled = false; btn.innerText = 'Claim Now'; }
    } finally {
        _pendingRequests.delete('claimDaily');
    }
}

// ============================================================
// MAIN DATA FETCH
// ============================================================
async function fetchLiveData() {
    if (!userId) {
        const bal = document.getElementById('balance');
        if (bal) bal.innerText = "ID Error";
        return;
    }
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_user/${userId}`);
        const data = await res.json();

        if (data.status === "blocked") {
            showBlockedView();
            return;
        }

        if (data.status === "success") {
            userData = data;

            const coins    = data.coins || 0;
            const refCount = getRefCount(data.referrals);

            const balEl = document.getElementById('balance');
            if (balEl) balEl.innerText = `${coins} 🪙`;

            // Progress bars — based on 5000 coins minimum withdrawal
            const coinsPct = Math.min((coins / MIN_WITHDRAW_COINS) * 100, 100);
            const refPct   = Math.min((refCount / 5) * 100, 100);

            const coinsBar  = document.getElementById('coins-progress-bar');
            const refBar    = document.getElementById('ref-progress-bar');
            const coinsText = document.getElementById('coins-progress-text');
            const refText   = document.getElementById('ref-progress-text');

            if (coinsBar) {
                coinsBar.style.width      = coinsPct + '%';
                coinsBar.style.background = coins >= MIN_WITHDRAW_COINS
                    ? 'linear-gradient(90deg,#2ecc71,#27ae60)'
                    : 'linear-gradient(90deg,#f1c40f,#f39c12)';
            }
            if (refBar)    refBar.style.width = refPct + '%';
            if (coinsText) coinsText.innerText = `${coins} / ${MIN_WITHDRAW_COINS}${coins >= MIN_WITHDRAW_COINS ? ' ✅' : ''}`;
            if (refText)   refText.innerText   = `${refCount} / 5${refCount >= 5 ? ' ✅' : ''}`;

            // Leaderboard
            if (data.leaderboard && data.leaderboard !== "none") {
                updateLeaderboardUI(data.leaderboard);
            }

            const linkEl = document.getElementById('display-link');
            if (linkEl) linkEl.innerText = `https://t.me/${CONFIG.BOT_USERNAME}?start=${userId}`;

            updateReferralList(data.referrals);
            applyCompletedTasks(data.completed_tasks || []);
            checkDailyBonus(data.last_claim);
            updateAdCounter(data.ads_today || 0, data.ads_date || "");
            updateChannelButtons(data.channel_claims || {});
            renderSponsorSlots(data.channel_claims || {}, data.completed_tasks || [], data.verify_completions || {});

            // Promo task completions (for index.html inline scripts)
            window._promoTaskCompletions = data.promo_task_completions || [];

            // All-tasks bonus checklist
            updateAllBonusUI(data);

            // Promo tasks (inline script in index.html)
            if (typeof loadPromoTasks === 'function') loadPromoTasks();

            // Lottery card refresh
            loadLotteryStatus();
        }
    } catch (err) {
        showToast("⚠️ Connection error. Retrying...", "error");
        setTimeout(fetchLiveData, 15000);
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

    // ── Frontend config lock ──
    // Set CONFIG.LOTTERY_ACTIVE = false in config.js to show lock animation.
    if (CONFIG.LOTTERY_ACTIVE === false) {
        card.style.display = 'block';
        if (!card.querySelector('.lottery-lock-overlay')) {
            const ov = document.createElement('div');
            ov.className = 'lottery-lock-overlay';
            ov.innerHTML = `
                <span class="lottery-lock-icon">🔒</span>
                <span class="lottery-lock-label">Lottery Coming Soon!</span>
                <span class="lottery-lock-sub">Stay tuned for updates</span>`;
            card.appendChild(ov);
        }
        return;
    }

    // Remove any stale lock overlay (in case config was just unlocked)
    const staleOv = card.querySelector('.lottery-lock-overlay');
    if (staleOv) staleOv.remove();

    try {
        const res  = await fetch(`${CONFIG.API_BASE_URL}/get_lottery_status?user_id=${userId}`);
        const data = await res.json();
        if (data.status !== 'success') {
            card.style.display = 'none';
            return;
        }
        if (!data.active) {
            card.style.display = 'none';
            return;
        }

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
            if (data.last_winner && data.last_winner.user_id) {
                const wid   = String(data.last_winner.user_id);
                const masked = wid.length > 4 ? `***${wid.slice(-4)}` : wid;
                winnerEl.innerText   = `🏆 Last winner: ${masked} won ${data.last_winner.prize} 🪙`;
                winnerEl.style.display = 'block';
            } else {
                winnerEl.style.display = 'none';
            }
        }

        if (btn) {
            if (data.drawn) {
                btn.disabled = true;
                btn.innerText = '🎲 Today\'s round drawn — back at 00:00 UTC';
                btn.style.background = '#7f8c8d';
                btn.style.color = '#fff';
            } else if (data.has_ticket) {
                btn.disabled = true;
                btn.innerText = '✅ You\'re in! Good luck 🍀';
                btn.style.background = '#27ae60';
                btn.style.color = '#fff';
            } else {
                btn.disabled = false;
                btn.innerText = `🎫 Buy Ticket (${data.ticket_price} 🪙)`;
                btn.style.background = '#ffd700';
                btn.style.color = '#1a1a1a';
            }
        }
    } catch (err) {
        // Silent fail — lottery card just won't show
        card.style.display = 'none';
    }
}

async function buyLotteryTicket() {
    if (!userId) {
        showToast('⚠️ User ID error.', 'error');
        return;
    }
    const btn = document.getElementById('lottery-btn');
    if (btn && btn.disabled) return;

    if (btn) {
        btn.disabled = true;
        btn.innerText = '⏳ Buying...';
    }

    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/buy_lottery_ticket`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId }),
        });
        const data = await res.json();

        if (data.status === 'success') {
            showToast(data.message || '🎫 Ticket purchased!', 'success');
            // Refresh balance + lottery card
            if (typeof refreshBalance === 'function') refreshBalance();
            loadLotteryStatus();
        } else {
            showToast(data.message || 'Could not buy ticket.', 'error');
            loadLotteryStatus();
        }
    } catch (err) {
        showToast('⚠️ Network error. Try again.', 'error');
        loadLotteryStatus();
    }
}

// ============================================================
// BALANCE REFRESH (called after ad/task claims)
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
// LEADERBOARD AUTO-REFRESH (every 10 minutes)
// ============================================================
async function refreshLeaderboard() {
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_leaderboard`);
        const data = await res.json();
        if (data.status === "success" && data.leaderboard) {
            updateLeaderboardUI(data.leaderboard);
        }
    } catch (e) { /* Silent */ }
}

// ============================================================
// PROMO CODE
// ============================================================
async function redeemPromo() {
    if (!userId) return showToast("User ID not found!", "error");

    const inputEl = document.getElementById('promo-input');
    const code    = inputEl ? inputEl.value.trim().toUpperCase() : '';

    if (!code) return showToast("Please enter a promo code!", "error");

    const reqKey = 'redeemPromo';
    if (_pendingRequests.has(reqKey)) return;
    _pendingRequests.add(reqKey);

    const btn = document.getElementById('promo-btn');
    if (btn) { btn.disabled = true; btn.innerText = "Checking..."; }

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/redeem_promo`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ user_id: userId, code: code })
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
        _pendingRequests.delete(reqKey);
        if (btn) { btn.disabled = false; btn.innerText = "Redeem"; }
    }
}

// ============================================================
// WITHDRAW
// ============================================================
async function requestWithdraw() {
    if (!userId) return showToast("User ID not found!", "error");
    if (_pendingRequests.has('withdraw')) return showToast("Request already in progress...", "error");

    const upi       = document.getElementById('upi-id')?.value.trim();
    const amountEl  = document.getElementById('withdraw-amount');
    const rawAmount = amountEl ? amountEl.value.trim() : '';
    const reqAmount = parseInt(rawAmount);
    const totalCoins = userData.coins || 0;

    if (!rawAmount)                        return showToast("Please enter the coin amount!", "error");
    if (isNaN(reqAmount))                  return showToast("Please enter a valid number!", "error");
    if (reqAmount <= 0)                    return showToast("Amount cannot be zero or negative!", "error");
    if (reqAmount < MIN_WITHDRAW_COINS)    return showToast(`Minimum ${MIN_WITHDRAW_COINS} coins required.`, "error");
    if (reqAmount > totalCoins)            return showToast(`Insufficient balance. You have ${totalCoins} coins.`, "error");
    if (!upi || !upi.includes('@'))        return showToast("Please enter a valid UPI ID! (Example: name@upi)", "error");

    _pendingRequests.add('withdraw');
    const btn = document.querySelector('[onclick="requestWithdraw()"]');
    if (btn) { btn.disabled = true; btn.innerText = "Processing..."; }

    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/withdraw`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ user_id: userId, upi_id: upi, amount: reqAmount })
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast(`💸 Withdrawal request submitted for ${reqAmount} coins!`, "success");
            const upiEl = document.getElementById('upi-id');
            if (upiEl)    upiEl.value = '';
            if (amountEl) amountEl.value = '';
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
// TASKS — Daily reset, 10s countdown on verify
// ============================================================
function openTask(taskKey, type) {
    const link = type === 'yt'      ? CONFIG.YT_LINKS[taskKey]
               : type === 'partner' ? CONFIG.PARTNER_LINKS?.[taskKey]
               : CONFIG.WEB_LINKS[taskKey];
    if (link && link !== '#') {
        window.open(link, '_blank');
    } else {
        showToast("Link will be updated soon!", "error");
    }
}

async function verifyTask(taskId, inputId, sponsorLink) {
    const code = document.getElementById(inputId)?.value.trim();
    if (!code) return showToast("Please enter the code!", "error");

    const reqKey    = `verify_${taskId}`;
    if (_pendingRequests.has(reqKey)) return;
    _pendingRequests.add(reqKey);

    // Accept both 2-arg (legacy) and 3-arg invocations. Resolve link from
    // CONFIG.SPONSORS if not explicitly passed (covers slot* IDs).
    let linkToSend = sponsorLink || "";
    if (!linkToSend && typeof CONFIG !== 'undefined' && CONFIG.SPONSORS && CONFIG.SPONSORS[taskId]) {
        linkToSend = CONFIG.SPONSORS[taskId].link || "";
    }

    const verifyBtn = document.querySelector(`[data-verify-btn="${taskId}"]`)
                   || document.querySelector(`[onclick^="verifyTask('${taskId}'"]`);
    if (verifyBtn) verifyBtn.disabled = true;

    startCountdown(10,
        (s) => { if (verifyBtn) verifyBtn.innerText = `Wait ${s}s...`; },
        async () => {
            // Ad gate — must watch before coins credited
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
                    body:    JSON.stringify({ user_id: userId, task_id: taskId, code: code, link: linkToSend })
                });
                const data = await res.json();
                if (data.status === "success") {
                    showToast(`✅ ${data.message}`, "success");
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
            btn.disabled         = true;
            btn.innerText        = "✅ Joined";
            btn.style.background = "#2ecc71";
        }
    });

    const slot1Btn = document.getElementById('ch-btn-slot1');
    if (slot1Btn && CONFIG.SPONSORS?.slot1?.active) {
        const claim = channelClaims['slot1'];
        const currentLink = CONFIG.SPONSORS.slot1.link || '';
        let alreadyClaimed = false;
        if (claim) {
            if (typeof claim === 'object' && claim.claimed_link) {
                alreadyClaimed = (claim.claimed_link === currentLink && currentLink !== '');
            } else if (claim === true) {
                alreadyClaimed = true;
            }
        }
        if (alreadyClaimed) {
            slot1Btn.disabled         = true;
            slot1Btn.innerText        = "✅ Joined";
            slot1Btn.style.background = "#2ecc71";
            slot1Btn.onclick          = null;
        }
    }

    const slot2Btn = document.getElementById('ch-btn-slot2');
    if (slot2Btn && CONFIG.SPONSORS?.slot2?.active) {
        const claim = channelClaims['slot2'];
        const currentLink = CONFIG.SPONSORS.slot2.link || '';
        let alreadyClaimed = false;
        if (claim) {
            if (typeof claim === 'object' && claim.claimed_link) {
                alreadyClaimed = (claim.claimed_link === currentLink && currentLink !== '');
            } else if (claim === true) {
                alreadyClaimed = true;
            }
        }
        if (alreadyClaimed) {
            slot2Btn.disabled         = true;
            slot2Btn.innerText        = "✅ Joined";
            slot2Btn.style.background = "#2ecc71";
            slot2Btn.onclick          = null;
        }
    }
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
// CHANNEL CLAIM — 15s countdown, 3-retry backend
// ============================================================
async function claimChannel(channelId, channelUrl) {
    if (!userId) return showToast("User ID not found!", "error");

    const reqKey = `channel_${channelId}`;
    if (_pendingRequests.has(reqKey)) return;

    if (channelId === 'slot1' || channelId === 'slot2' || channelId === 'slot3' || channelId === 'slot4') {
        trackSponsorClick(channelId, channelUrl);
    }

    window.open(channelUrl, '_blank');

    _pendingRequests.add(reqKey);
    const btn = document.getElementById(`ch-btn-${channelId}`);
    if (btn) btn.disabled = true;

    startCountdown(15,
        (s) => { if (btn) btn.innerText = `Join & wait ${s}s...`; },
        async () => {
            // Ad gate — must watch before coins credited
            if (btn) { btn.disabled = true; btn.innerText = '📺 Watch Ad...'; }
            try {
                await requireAdWatch();
            } catch (e) {
                showToast('📺 Watch the full ad to claim your reward!', 'error');
                if (btn) {
                    btn.disabled  = false;
                    btn.innerText = '🔄 Retry';
                    btn.onclick   = () => claimChannel(channelId, channelUrl);
                }
                _pendingRequests.delete(reqKey);
                return;
            }
            if (btn) btn.innerText = 'Claiming...';
            try {
                const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/claim_channel`, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({
                        user_id:      userId,
                        channel_id:   channelId,
                        channel_url:  channelUrl,
                        claimed_link: channelUrl
                    })
                });
                const data = await res.json();

                if (data.status === "success") {
                    showToast(`🎉 ${data.message}`, "success");
                    if (btn) {
                        btn.disabled         = true;
                        btn.innerText        = "✅ Joined";
                        btn.style.background = "#2ecc71";
                        btn.onclick          = null;
                    }
                    fetchLiveData();

                } else if (data.status === "not_joined") {
                    showToast("❌ Join not confirmed! Make sure you joined, then tap Retry.", "error");
                    if (btn) {
                        btn.disabled         = false;
                        btn.innerText        = "🔄 Retry";
                        btn.style.background = "#e74c3c";
                        btn.onclick = () => {
                            btn.style.background = '';
                            btn.innerText        = "Join & Claim";
                            btn.onclick          = () => claimChannel(channelId, channelUrl);
                            claimChannel(channelId, channelUrl);
                        };
                    }
                } else {
                    showToast(data.message, "error");
                    if (btn) {
                        btn.disabled         = false;
                        btn.innerText        = "Join & Claim";
                        btn.style.background = '';
                        btn.onclick          = () => claimChannel(channelId, channelUrl);
                    }
                }
            } catch (e) {
                showToast("⚠️ Connection error! Please retry.", "error");
                if (btn) {
                    btn.disabled  = false;
                    btn.innerText = "🔄 Retry";
                    btn.onclick   = () => claimChannel(channelId, channelUrl);
                }
            } finally {
                _pendingRequests.delete(reqKey);
            }
        }
    );
}

// ============================================================
// AD COUNTER — Updated to 10 ads/day
// ============================================================
function updateAdCounter(adsToday, adsDate) {
    const today   = new Date().toISOString().split('T')[0];
    const done    = (adsDate === today) ? Math.min(adsToday, MAX_ADS_PER_DAY) : 0;

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

    // Daily bonus: check if last_claim was today (UTC)
    const lastClaim = data.last_claim || '';
    const lastClaimDt = parseUTCTimestamp(lastClaim);
    const dailyDone = lastClaimDt
        ? (lastClaimDt.toISOString().slice(0, 10) === today)
        : false;

    // Ads done today
    const adsDate   = data.ads_date || '';
    const adsToday  = (adsDate === today) ? (data.ads_today || 0) : 0;
    const adsFull   = adsToday >= MAX_ADS_PER_DAY;

    // Task completions
    const completed = data.completed_tasks || [];
    const ytDone    = ['yt1','yt2','yt3'].filter(t => completed.includes(t)).length;
    const webDone   = ['web1','web2','web3'].filter(t => completed.includes(t)).length;

    // Bonus already claimed today?
    const bonusDate     = data.allcomplete_bonus_date || '';
    const alreadyClaimed = (bonusDate === today);

    // Update checkmarks
    const setCheck = (id, done) => {
        const el = document.getElementById(id);
        if (el) el.textContent = done ? '✅' : '⬜';
    };
    setCheck('check-daily', dailyDone);
    setCheck('check-ads',   adsFull);
    setCheck('check-yt',    ytDone  >= MAX_YT_PER_DAY);
    setCheck('check-web',   webDone >= MAX_WEB_PER_DAY);

    // Update counts
    const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
    setText('allbonus-ads-count', `(${Math.min(adsToday, MAX_ADS_PER_DAY)}/${MAX_ADS_PER_DAY})`);
    setText('allbonus-yt-count',  `(${ytDone}/${MAX_YT_PER_DAY})`);
    setText('allbonus-web-count', `(${webDone}/${MAX_WEB_PER_DAY})`);

    // Badge
    const doneCount = [dailyDone, adsFull, ytDone >= MAX_YT_PER_DAY, webDone >= MAX_WEB_PER_DAY].filter(Boolean).length;
    const badge = document.getElementById('allbonus-status-badge');
    if (badge) badge.textContent = `${doneCount}/4`;

    // Claim button
    const allDone = dailyDone && adsFull && ytDone >= MAX_YT_PER_DAY && webDone >= MAX_WEB_PER_DAY;
    const btn = document.getElementById('allbonus-btn');
    if (!btn) return;

    if (alreadyClaimed) {
        btn.disabled    = true;
        btn.innerText   = '✅ Bonus Claimed Today!';
        btn.style.background = '#334155';
    } else if (allDone) {
        btn.disabled    = false;
        btn.innerText   = '🏅 Claim Bonus 10 Coins';
        btn.style.background = 'linear-gradient(135deg,#22c55e,#16a34a)';
    } else {
        btn.disabled    = true;
        btn.innerText   = `🏅 Complete All Tasks (${doneCount}/4)`;
        btn.style.background = '#1e3a1e';
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
        const res  = await fetchWithRetry(
            `${CONFIG.API_BASE_URL}/claim_allcomplete_bonus/${userId}`,
            { method: 'POST' }
        );
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
// MANDATORY AD GATE — called before every coin claim
// Resolves silently if ads disabled / not configured.
// Throws 'ad_skipped' if user does not watch the full ad.
// ============================================================
async function requireAdWatch() {
    if (!CONFIG.CLAIM_AD_ENABLED) return;
    const zoneId = getMonetagZoneId();
    if (!zoneId) return; // no ad zone configured — skip silently
    try {
        await loadMonetagSdk();
    } catch (e) {
        return; // SDK failed to load — allow claim anyway
    }
    const showMonetagAd = getMonetagShowFunction();
    if (!showMonetagAd) return; // function not ready — skip silently
    const result = await showMonetagAd({ ymid: String(userId), requestVar: 'claim_gate' });
    if (!result?.reward_event_type || result.reward_event_type !== 'valued') {
        throw new Error('ad_skipped');
    }
}

// ============================================================
// MONETAG REWARDED AD — coins only after full ad completion
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

        const tokenRes = await fetchWithRetry(
            `${CONFIG.API_BASE_URL}/ad_claim_token/${userId}`,
            { method: 'POST' }
        );
        const tokenData = await tokenRes.json();
        if (tokenData.status !== "success" || !tokenData.token) {
            showToast(tokenData.message || "Ad reward is not available right now.", "error");
            return;
        }

        if (btn) btn.innerText = monetagPreloaded ? "Showing Ad..." : "Preparing Ad...";
        const adResult = await showMonetagAd({
            ymid: String(userId),
            requestVar: 'ad_reward',
        });
        monetagPreloaded = false;

        if (!adResult?.reward_event_type || adResult.reward_event_type !== 'valued') {
            showToast("Ad was skipped. Watch the full ad to earn coins.", "error");
            preloadMonetagAd();
            return;
        }

        if (btn) btn.innerText = "Crediting...";
        const res = await fetchWithRetry(
            `${CONFIG.API_BASE_URL}/claim_ad/${userId}`,
            {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ token: tokenData.token })
            }
        );
        const data = await res.json();
        const adsDone = data.data?.ads_done ?? data.ads_done;

        if (data.status === "success") {
            showToast(`✅ ${data.message}`, "success");
            // Update counter immediately without full reload
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
// DEVICE CHECK
// ============================================================
// Strong device fingerprint via FingerprintJS open source (50+ signals).
// Falls back to a weak hash (prefixed `wk_`) only if the CDN is blocked.
let _fpPromise = null;
function loadFingerprintJS() {
    if (_fpPromise) return _fpPromise;
    _fpPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'https://openfpcdn.io/fingerprintjs/v4/iife.min.js';
        s.async = true;
        s.onload  = () => resolve(window.FingerprintJS);
        s.onerror = () => reject(new Error('FingerprintJS failed to load'));
        document.head.appendChild(s);
    }).catch((err) => { _fpPromise = null; throw err; });
    return _fpPromise;
}

async function generateFingerprint() {
    try {
        const FP = await loadFingerprintJS();
        const fp = await FP.load();
        const result = await fp.get();
        if (result?.visitorId) return result.visitorId;
        throw new Error('no visitorId');
    } catch (e) {
        // Fallback (weak) — better than nothing if CDN blocked
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
            return "wk_" + Array.from(new Uint8Array(buf))
                .map(b => b.toString(16).padStart(2, "0")).join("");
        } catch (_) { return ""; }
    }
}

async function checkDevice() {
    if (!userId) return;
    try {
        const fingerprint = await generateFingerprint();
        if (!fingerprint) return; // empty fp backend ko mat bhejo
        const res = await fetch(`${CONFIG.API_BASE_URL}/check_device`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ user_id: userId, fingerprint })
        });
        const data = await res.json();
        if (data.status === "blocked") {
            showBlockedView();
        }
    } catch (e) { /* Silent */ }
}

// ============================================================
// LEADERBOARD
// ============================================================
function updateLeaderboardUI(leaderboardData) {
    const list = document.getElementById('leaderboard-list');
    if (!list) return;
    if (!leaderboardData || leaderboardData === "none") {
        list.innerHTML = "<p class='spinner'>No users yet.</p>";
        return;
    }
    const medals  = ['🥇', '🥈', '🥉'];
    const players = leaderboardData.split('|');
    let html = "";
    players.forEach((p, i) => {
        const [id, coins] = p.split(':');
        const isMe = String(id) === String(userId);
        html += `
            <div class="lb-item" style="${isMe ? 'background:rgba(99,102,241,0.1);border-radius:8px;padding:10px;' : ''}">
                <span class="lb-rank">${medals[i] || `#${i + 1}`}</span>
                <span class="lb-user">${isMe ? '👤 You' : `User ${id}`}</span>
                <span class="lb-coins">${parseInt(coins) || 0} 🪙</span>
            </div>`;
    });
    list.innerHTML = html;
}

// ============================================================
// REFERRAL LIST
// ============================================================
function updateReferralList(referrals) {
    const list = document.getElementById('refer-list');
    if (!list) return;
    const refCount = getRefCount(referrals);
    if (refCount === 0) {
        list.innerHTML = "<p style='color:#94a3b8;text-align:center;font-size:13px;'>No referrals yet. Invite your friends! 🚀</p>";
        return;
    }
    const refs = referrals.split(',').filter(id => id.trim() !== '');
    let html = "";
    refs.forEach((id, i) => {
        html += `
            <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #1e293b;">
                <span style="font-size:16px;">👤</span>
                <div>
                    <p style="margin:0;font-size:13px;font-weight:600;color:#e2e8f0;">Friend ${i + 1}</p>
                    <p style="margin:0;font-size:11px;color:#94a3b8;">ID: ${id.trim()}</p>
                </div>
                <span style="margin-left:auto;font-size:12px;color:#2ecc71;font-weight:700;">+30 🪙</span>
            </div>`;
    });
    list.innerHTML = html;
}

// ============================================================
// WITHDRAWAL HISTORY
// ============================================================
async function loadHistory() {
    const list = document.getElementById('history-list');
    if (!list || !userId) return;
    list.innerHTML = "<p class='spinner'>Loading...</p>";
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_history/${userId}`);
        const data = await res.json();
        const history = data.history || data.data?.history;
        if (history && history.length > 0) {
            let html = "";
            history.forEach(h => {
                const color = h.status.includes('Approved') ? '#22c55e'
                            : h.status.includes('Rejected') ? '#e74c3c' : '#f1c40f';
                html += `
                    <div class="history-item">
                        <div>💸 <b>${h.amount} coins</b> — UPI: ${h.upi_id}</div>
                        <div class="history-status" style="color:${color}">${h.status} • ${h.date}</div>
                    </div>`;
            });
            list.innerHTML = html;
        } else {
            list.innerHTML = "<p style='color:#94a3b8;text-align:center;'>No withdrawal history found.</p>";
        }
    } catch (e) {
        list.innerHTML = "<p style='color:#94a3b8;text-align:center;'>Failed to load history.</p>";
    }
}

// ============================================================
// SUPPORT — 1 message per day
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
// UTILITY FUNCTIONS
// ============================================================
function showBlockedView() {
    document.querySelectorAll('.tab-content').forEach(el => {
        el.style.display = 'none';
        el.classList.remove('active-tab');
    });

    const nav = document.querySelector('.bottom-nav');
    if (nav) nav.style.display = 'none';

    const helpTab = document.getElementById('help');
    if (helpTab) {
        helpTab.style.display = 'block';
        helpTab.classList.add('active-tab');
    }

    const banner = document.getElementById('blocked-banner');
    if (banner) banner.style.display = 'block';

    const titleEl = document.getElementById('tab-title');
    if (titleEl) titleEl.textContent = '🚫 Account Blocked';
}

function copyEmail() {
    navigator.clipboard.writeText('cdoternsupport@gmail.com').catch(() => {});
    const status = document.getElementById('copy-status');
    if (status) {
        status.style.display = 'block';
        setTimeout(() => { status.style.display = 'none'; }, 2000);
    }
}

async function inviteFriend() {
    if (!userId) return showToast("User ID not found!", "error");
    if (_pendingRequests.has('inviteFriend')) return;
    _pendingRequests.add('inviteFriend');

    const link = `https://t.me/${CONFIG.BOT_USERNAME}?start=${userId}`;
    const btn = document.querySelector('[onclick="inviteFriend()"]');
    if (btn) { btn.disabled = true; btn.innerText = "Opening..."; }

    try {
        const shareText = '💰 Earn coins daily by watching ads & completing tasks! 🚀 Join now and start earning instantly!';
        if (tg && tg.openTelegramLink) {
            tg.openTelegramLink(
                `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(shareText)}`
            );
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

function switchTab(tabId, el) {
    document.querySelectorAll('.tab-content').forEach(t => {
        t.style.display = 'none';
        t.classList.remove('active-tab');
    });
    const tab = document.getElementById(tabId);
    if (tab) { tab.style.display = 'block'; tab.classList.add('active-tab'); }

    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    if (el) el.classList.add('active');

    const titleMap = {
        rewards:     'Rewards',
        tasks:       'Daily Tasks',
        leaderboard: 'Top Earners',
        refer:       'Refer & Earn',
        history:     'Withdrawal History',
        help:        'Help & Support',
    };
    const titleEl = document.getElementById('tab-title');
    if (titleEl) titleEl.textContent = titleMap[tabId] || '';

    if (tabId === 'leaderboard') refreshLeaderboard();
    if (tabId === 'history')     loadHistory();
}

// ============================================================
// SPONSOR SLOTS — Dynamic render from CONFIG.SPONSORS
// ============================================================

/**
 * Render all sponsor slots (slot1–slot4) from CONFIG.SPONSORS.
 * Called on init and after fetchLiveData so claimed state is fresh.
 * @param {object} channelClaims   - user's channel_claims map from server
 * @param {Array}  completedTasks  - user's completed task IDs (for verify type)
 */
function renderSponsorSlots(channelClaims, completedTasks, verifyCompletions) {
    const container = document.getElementById('sponsor-slots-container');
    if (!container) return;

    const sponsors = CONFIG.SPONSORS || {};
    const slots    = ['slot1', 'slot2', 'slot3', 'slot4'];
    const claims   = channelClaims  || {};
    const done     = completedTasks || [];

    let html = '';

    slots.forEach(slotId => {
        const s = sponsors[slotId];
        if (!s) return; // slot not defined in config → skip

        const icon   = s.icon   || '💼';
        const name   = s.name   || ('Sponsor ' + slotId);
        const desc   = s.desc   || '';
        const link   = s.link   || '#';
        const reward = s.reward || 5;
        const type   = s.type   || 'channel';
        const active = s.active === true;

        if (!active) {
            // Locked slot
            html += `
            <div style="position:relative; display:flex; align-items:center; gap:12px; padding:10px;
                        background:rgba(255,255,255,0.04); border-radius:10px; margin-bottom:8px;
                        overflow:hidden; min-height:58px;">
                <div class="lock-overlay">
                    <span class="lock-icon">🔒</span>
                    <span class="lock-label">Slot Available</span>
                </div>
                <div style="font-size:26px;">${icon}</div>
                <div style="flex:1;">
                    <p style="font-size:13px; font-weight:600; color:#475569; margin:0;">${name}</p>
                    <p style="font-size:11px; color:#334155; margin:2px 0 0 0;">Contact admin to activate</p>
                </div>
                <button class="btn-sm" style="background:#38bdf8; color:#000; opacity:0.4;" disabled>Locked</button>
            </div>`;
            return;
        }

        // Check if already claimed
        const claim = claims[slotId];
        let alreadyClaimed = false;
        if (claim) {
            if (typeof claim === 'object' && claim.claimed_link) {
                alreadyClaimed = (claim.claimed_link === link && link !== '');
            } else if (claim === true) {
                alreadyClaimed = true;
            }
        }

        if (type === 'verify') {
            // Verify type — open site + enter code + verify (one-time per
            // (code,link) pair). If admin rotates either the code (via
            // /settask) or the sponsor link (in config.js), the slot
            // automatically re-opens for everyone.
            const vc = (verifyCompletions || {})[slotId] || {};
            const linkMatches = !vc.link || vc.link === link;
            const isVerifyDone = done.includes(slotId) && linkMatches;
            const inputId = `${slotId}-code-input`;
            html += `
            <div class="partner-card" style="margin-bottom:8px;">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
                    <span style="font-size:22px;">${icon}</span>
                    <div style="flex:1;">
                        <p style="font-size:13px; font-weight:700; color:#3498db; margin:0;">${name}</p>
                        <p style="font-size:11px; color:#94a3b8; margin:2px 0 0 0;">${desc}</p>
                    </div>
                    <span style="font-size:12px; color:#f1c40f; font-weight:700;">+${reward} 🪙</span>
                </div>
                ${isVerifyDone
                    ? `<button class="btn-sm" style="width:100%; background:#334155; color:#64748b;" disabled>✅ Completed (One-time)</button>`
                    : `<button class="btn-sm" style="background:#3498db; width:100%; margin-bottom:8px; font-weight:700;"
                            onclick="window.open('${link}', '_blank')">
                            🌐 Visit Site
                        </button>
                        <div style="display:flex; gap:8px;">
                            <input type="text" id="${inputId}" placeholder="Enter code"
                                style="flex:1; padding:8px 10px; background:#1e293b; border:1px solid #334155;
                                       border-radius:8px; color:#e2e8f0; font-size:13px; text-transform:uppercase;"
                                maxlength="20">
                            <button class="btn-sm" data-verify-btn="${slotId}"
                                style="background:linear-gradient(135deg,#3498db,#2980b9); font-weight:700;"
                                onclick="verifyTask('${slotId}', '${inputId}', '${link}')">Verify</button>
                        </div>`
                }
            </div>`;

        } else if (type === 'task') {
            // Task type — open link + claim button (no code)
            html += `
            <div class="partner-card" style="margin-bottom:8px;">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
                    <span style="font-size:22px;">${icon}</span>
                    <div style="flex:1;">
                        <p style="font-size:13px; font-weight:700; color:#a855f7; margin:0;">${name}</p>
                        <p style="font-size:11px; color:#94a3b8; margin:2px 0 0 0;">${desc}</p>
                    </div>
                    <span style="font-size:12px; color:#f1c40f; font-weight:700;">+${reward} 🪙</span>
                </div>
                ${alreadyClaimed
                    ? `<button class="btn-sm" style="width:100%; background:#334155; color:#64748b;" disabled>✅ Completed</button>`
                    : `<button class="btn-sm" style="width:100%; background:linear-gradient(135deg,#a855f7,#7c3aed); color:#fff; font-weight:700;"
                            onclick="claimChannel('${slotId}', '${link}')">
                            Open & Claim +${reward} 🪙
                        </button>`
                }
            </div>`;
        } else {
            // Channel type — join button
            html += `
            <div style="display:flex; align-items:center; gap:12px; padding:10px;
                        background:rgba(255,255,255,0.05); border-radius:10px; margin-bottom:8px;">
                <div style="font-size:26px;">${icon}</div>
                <div style="flex:1;">
                    <p style="font-size:13px; font-weight:600; color:#e2e8f0; margin:0;">${name}</p>
                    <p style="font-size:11px; color:#94a3b8; margin:2px 0 0 0;">${desc}</p>
                </div>
                ${alreadyClaimed
                    ? `<button class="btn-sm" style="background:#2ecc71; color:#000;" disabled>✅ Joined</button>`
                    : `<button id="ch-btn-${slotId}" class="btn-sm ch-claim-btn"
                            style="background:linear-gradient(135deg,#38bdf8,#0ea5e9); color:#000; font-weight:700;"
                            onclick="claimChannel('${slotId}', '${link}')">
                            +${reward} 🪙 Join
                        </button>`
                }
            </div>`;
        }
    });

    container.innerHTML = html || '<p style="color:#475569; text-align:center; font-size:13px;">No sponsor slots configured.</p>';
}

// ============================================================
// APP INIT
// ============================================================
window.addEventListener('DOMContentLoaded', () => {
    // Set admin Telegram username display
    const adminEl = document.getElementById('admin-tg-username');
    if (adminEl && CONFIG.ADMIN_TELEGRAM) {
        const u = String(CONFIG.ADMIN_TELEGRAM);
        adminEl.textContent = u.startsWith('@') ? u : '@' + u;
    }

    renderSponsorSlots({}, [], {});
    fetchLiveData();
    checkDevice();
    preloadMonetagAd();

    // Auto-refresh data every 5 minutes
    setInterval(fetchLiveData, 300000);

    // Leaderboard refresh every 10 minutes
    setInterval(refreshLeaderboard, 600000);
});
