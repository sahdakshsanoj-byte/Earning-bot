// ============================================================
// SCRIPT.JS — Daksh Grand Earn (Clean Rewrite)
// ============================================================

const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();
tg.enableClosingConfirmation();

const userId = tg.initDataUnsafe?.user?.id;

window.USER_ID = userId;
// Store full user object for tournament + other features
window._tgUser = tg.initDataUnsafe?.user || null;

let userData = {};
let _winnerPopupShown = false;   // guard: show winner popup only once per session
const _pendingRequests = new Set();
let monetagSdkPromise  = null;
let monetagPreloaded   = false;

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
    if (!userId) {
        const bal = document.getElementById('balance');
        if (bal) bal.innerText = "ID Error";
        return;
    }
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_user/${userId}`);
        const data = await res.json();

        if (data.status === "blocked") { showBlockedView(); return; }

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

            if (data.leaderboard && data.leaderboard !== "none") updateLeaderboardUI(data.leaderboard);

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

            // Update withdraw minimum check with premium dynamic value
            window._dynamicMinWithdraw = isPremium ? 10000 : MIN_WITHDRAW_COINS;

            // Update premium card on home tab
            updatePremiumCard(premInfo);

            if (data.pending_winner_popup && !_winnerPopupShown) showWinnerPopup(data.pending_winner_prize || 0);
        }
    } catch (err) {
        showToast("⚠️ Connection error. Retrying...", "error");
        setTimeout(fetchLiveData, 15000);
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
    if (card.querySelector('.' + overlayClass)) return;
    if (getComputedStyle(card).position === 'static') card.style.position = 'relative';
    card.style.overflow = 'hidden';
    const ov = document.createElement('div');
    ov.className = overlayClass;
    ov.style.cssText =
        'position:absolute;inset:0;display:flex;flex-direction:column;' +
        'align-items:center;justify-content:center;' +
        'background:rgba(15,23,42,0.88);border-radius:16px;' +
        'z-index:10;backdrop-filter:blur(3px);pointer-events:all;cursor:default;';
    ov.innerHTML =
        '<span style="font-size:36px;animation:lock-pulse 1.6s ease-in-out infinite;display:block;">🔒</span>' +
        '<span style="font-size:13px;color:#e8d5ff;margin-top:8px;font-weight:700;letter-spacing:0.5px;">' + label + '</span>' +
        '<span style="font-size:11px;color:#94a3b8;margin-top:3px;">Coming Soon</span>';
    card.appendChild(ov);
}

function _removeFeatureLock(card, overlayClass) {
    if (!card) return;
    const ov = card.querySelector('.' + overlayClass);
    if (ov) ov.remove();
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
        const cfgRes = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_feature_config`);
        const cfg    = await cfgRes.json();

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
            if (labelEl)    labelEl.innerText    = '✅ Mining Complete! Collect your reward!';
            if (collectBtn) {
                collectBtn.disabled  = false;
                collectBtn.innerText = '⛏️ Collect 10 Coins!';
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
        const cfgRes = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_feature_config`);
        const cfg    = await cfgRes.json();

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

        if (data.collect_ready) {
            // Mining done — ready to collect
            if (_miningInterval) { clearInterval(_miningInterval); _miningInterval = null; }
            if (statusEl)   statusEl.innerText   = '✅ Mining Complete! Collect your reward!';
            if (collectBtn) {
                collectBtn.style.display  = '';
                collectBtn.disabled       = false;
                collectBtn.innerText      = '⛏️ Collect 10 Coins!';
                collectBtn.style.background = 'linear-gradient(135deg,#22c55e,#16a34a)';
            }

        } else if (data.is_mining) {
            // Mining in progress
            if (statusEl)   statusEl.innerText  = '⛏️ Mining in progress...';
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

        } else {
            // Idle — show watch ad button
            const adsLeft = (data.ads_required || 2) - (data.ads_done || 0);
            if (watchBtn) {
                watchBtn.style.display = '';
                watchBtn.disabled      = false;
                watchBtn.innerText     = `📺 Watch Ad ${data.ads_done || 0}/${data.ads_required || 2}`;
            }
            if (statusEl) statusEl.innerText = `Watch ${adsLeft} more ad${adsLeft !== 1 ? 's' : ''} to start mining!`;
            if (adsEl)    adsEl.innerText    = `${data.ads_done || 0}/${data.ads_required || 2} ads watched`;
        }
    } catch (e) { /* silent */ }
}

async function watchMiningAd() {
    if (!userId) return showToast('User ID not found!', 'error');
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
            showToast('⛏️ Mining started! Come back in 1 hour to collect 10 coins!', 'success');
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
            if (btn) { btn.disabled = false; btn.innerText = '⛏️ Collect 10 Coins!'; }
        }
    } catch (e) {
        showToast('⚠️ Error! Please retry.', 'error');
        if (btn) { btn.disabled = false; btn.innerText = '⛏️ Collect 10 Coins!'; }
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
        const cfgRes = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_feature_config`);
        const cfg    = await cfgRes.json();
        if (!cfg.web_tasks_active) {
            _applyFeatureLock(card, 'web-tasks-lock-overlay', '🌐 Web Tasks Coming Soon!');
        } else {
            _removeFeatureLock(card, 'web-tasks-lock-overlay');
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
    try {
        const cfgRes = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_feature_config`);
        const cfg    = await cfgRes.json();
        if (!cfg.premium_active) {
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

async function loadBombBoxStatus() {
    if (!userId) return;
    const card = document.getElementById('bomb-box-card');
    if (!card) return;

    try {
        const cfgRes = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_feature_config`);
        const cfg    = await cfgRes.json();

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
async function refreshLeaderboard() {
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_leaderboard`);
        const data = await res.json();
        if (data.status === "success" && data.leaderboard) updateLeaderboardUI(data.leaderboard);
    } catch (e) { /* silent */ }
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
        const [id, coins] = p.split(':');
        const isMe = String(id) === String(userId);
        return `
            <div class="lb-item" style="${isMe ? 'background:rgba(99,102,241,0.1);border-radius:8px;padding:10px;' : ''}">
                <span class="lb-rank">${medals[i] || `#${i + 1}`}</span>
                <span class="lb-user">${isMe ? '👤 You' : `User ${id}`}</span>
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
    const colors = { upi: '#2ecc71', usdt: '#3b82f6', google: '#f59e0b' };
    const bg     = { upi: '#0d2318',  usdt: '#0d1b2e', google: '#1c1600' };
    ['upi', 'usdt', 'google'].forEach(m => {
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

    // Referral check — 5 referrals required when CONFIG.REFERRAL_ACTIVE is true
    // BUG FIX: removed duplicate const refCount, using outer refCount
    if (CONFIG.REFERRAL_ACTIVE === true) {
        if (refCount < 5) {
            return showToast(`You need ${5 - refCount} more referral(s) to unlock withdrawal.`, "error");
        }
    }

    let paymentAddress = '';
    if (method === 'upi') {
        const upi = document.getElementById('upi-id')?.value.trim();
        if (!upi || !upi.includes('@')) return showToast("Please enter a valid UPI ID! (Example: name@upi)", "error");
        paymentAddress = upi;
    } else if (method === 'usdt') {
        const addr = document.getElementById('usdt-address')?.value.trim();
        if (!addr) return showToast("Please enter your USDT TRC20 wallet address!", "error");
        if (!addr.startsWith('T') || addr.length !== 34 || !/^[A-Za-z0-9]{34}$/.test(addr))
            return showToast("Invalid TRC20 address! Must start with T and be 34 characters.", "error");
        paymentAddress = addr;
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
            const methodLabel = method === 'upi' ? 'UPI' : method === 'usdt' ? 'USDT TRC20' : 'Google Play';
            showToast(`💸 ${methodLabel} withdrawal request submitted!`, "success");
            if (amountEl) amountEl.value = '';
            const upiEl  = document.getElementById('upi-id');
            const usdtEl = document.getElementById('usdt-address');
            if (upiEl)  upiEl.value  = '';
            if (usdtEl) usdtEl.value = '';
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
    if (link && link !== '#') window.open(link, '_blank');
    else showToast("Link will be updated soon!", "error");
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

    window.open(channelUrl, '_blank');
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
        if (data.status === "blocked") showBlockedView();
    } catch (e) { /* silent */ }
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
    navigator.clipboard.writeText(link)
        .then(() => showToast('✅ Referral link copied!', 'success'))
        .catch(() => showToast('Copy failed, try again.', 'error'));
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
// WITHDRAWAL HISTORY
// ============================================================
async function loadHistory() {
    const list = document.getElementById('history-list');
    if (!list || !userId) return;
    list.innerHTML = "<p class='spinner'>Loading...</p>";
    try {
        const res     = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_history/${userId}`);
        const data    = await res.json();
        const history = data.history || data.data?.history;
        if (history && history.length > 0) {
            const methodIcons = { upi: '🏦', usdt_trc20: '💎', google_redeem: '🎁' };
            const methodNames = { upi: 'UPI', usdt_trc20: 'USDT TRC20', google_redeem: 'Google Play' };
            list.innerHTML = history.map(h => {
                const color  = h.status.includes('Approved') ? '#22c55e' : h.status.includes('Rejected') ? '#e74c3c' : '#f1c40f';
                const m      = h.method || 'upi';
                const addr   = h.payment_address || h.upi_id || '—';
                const addrDisplay = addr === 'via_telegram' ? 'via Telegram DM' : addr;
                return `
                    <div class="history-item">
                        <div>${methodIcons[m] || '💸'} <b>${h.amount} coins</b> — ${methodNames[m] || 'UPI'}: <span style="color:#94a3b8;font-size:12px;">${addrDisplay}</span></div>
                        <div class="history-status" style="color:${color}">${h.status} • ${h.date}</div>
                    </div>`;
            }).join('');
        } else {
            list.innerHTML = "<p style='color:#94a3b8;text-align:center;'>No withdrawal history found.</p>";
        }
    } catch (e) {
        list.innerHTML = "<p style='color:#94a3b8;text-align:center;'>Failed to load history.</p>";
    }
}

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
    document.querySelectorAll('.tab-content').forEach(el => { el.style.display = 'none'; el.classList.remove('active-tab'); });
    const nav = document.querySelector('.bottom-nav');
    if (nav) nav.style.display = 'none';
    const helpTab = document.getElementById('help');
    if (helpTab) { helpTab.style.display = 'block'; helpTab.classList.add('active-tab'); }
    const banner = document.getElementById('blocked-banner');
    if (banner) banner.style.display = 'block';
    const titleEl = document.getElementById('tab-title');
    if (titleEl) titleEl.textContent = '🚫 Account Blocked';
}

function copyEmail() {
    navigator.clipboard.writeText('cdoternsupport@gmail.com').catch(() => {});
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
    document.querySelectorAll('.tab-content').forEach(t => { t.style.display = 'none'; t.classList.remove('active-tab'); });
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
        spin:        '🎡 Spin Wheel',
        mining:      '⛏️ Coin Mining',
        tournament:  '🏆 Tournament Hub',
        'bomb-box':  '💣 Bomb Box',
    };
    const titleEl = document.getElementById('tab-title');
    if (titleEl) titleEl.textContent = titleMap[tabId] || '';

    if (tabId === 'leaderboard') refreshLeaderboard();
    if (tabId === 'history')     loadHistory();
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
                <div class="lock-overlay">
                    <span class="lock-icon">🔒</span>
                    <span class="lock-label">Slot Available</span>
                </div>
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
                            onclick="window.open('${link}', '_blank')">🌐 Visit Site</button>
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

    // ── CASE 1: Withdraw lock — referrals not yet completed ───────────────────────
    if (lockActive) {
        if (withdrawTab && !withdrawTab.querySelector('.refer-lock-overlay')) {
            const ov = document.createElement('div');
            ov.className = 'refer-lock-overlay';
            ov.style.cssText = [
                'position:absolute', 'inset:0', 'display:flex', 'flex-direction:column',
                'align-items:center', 'justify-content:center',
                'background:rgba(10,15,30,0.93)', 'backdrop-filter:blur(6px)',
                'border-radius:16px', 'z-index:9999', 'pointer-events:all', 'cursor:default',
            ].join(';');
            ov.innerHTML =
                '<span style="font-size:52px;animation:lock-pulse 1.8s ease-in-out infinite;display:block;">🔒</span>' +
                '<span style="font-size:16px;color:#f1c40f;font-weight:800;margin-top:14px;letter-spacing:0.5px;">5 Referrals Required</span>' +
                `<span style="font-size:13px;color:#94a3b8;margin-top:6px;">You have <b style="color:#e2e8f0;">${refCount}/5</b> referrals</span>` +
                `<span style="font-size:12px;color:#64748b;margin-top:4px;">Invite ${5 - refCount} more friend${5 - refCount > 1 ? 's' : ''} to unlock withdrawal</span>`;
            ov.addEventListener('click', e => e.stopPropagation());
            withdrawTab.appendChild(ov);
        } else if (withdrawTab) {
            const existing = withdrawTab.querySelector('.refer-lock-overlay');
            if (existing) {
                const spans = existing.querySelectorAll('span');
                if (spans[2]) spans[2].innerHTML = `You have <b style="color:#e2e8f0;">${refCount}/5</b> referrals`;
                if (spans[3]) spans[3].textContent = `Invite ${5 - refCount} more friend${5 - refCount > 1 ? 's' : ''} to unlock withdrawal`;
            }
        }
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
    }
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
        _renderTournament(cached.tournament, cached.winners);
        return;
    }

    if (content) content.innerHTML = '<div style="padding:20px;text-align:center;color:#64748b;font-size:12px;">⏳ Loading...</div>';

    try {
        const uid = window._tgUser?.id;
        const [tRes, regRes] = await Promise.all([
            fetchWithRetry(`${CONFIG.API_BASE_URL}/tournament/${encodeURIComponent(tid)}`),
            uid ? fetchWithRetry(`${CONFIG.API_BASE_URL}/tournament/my_registration/${uid}?tournament_id=${encodeURIComponent(tid)}`) : Promise.resolve(null),
        ]);

        const tData  = await tRes.json();
        const regData = regRes ? await regRes.json() : { registered: false };

        if (tData.status === 'success') {
            _tournamentCache[tid] = { tournament: tData.tournament, winners: tData.winners || [], ts: Date.now() };
        }
        _tournamentRegCache[tid] = (regData?.status === 'success') ? regData : { registered: false };

        _renderTournament(tData.tournament, tData.winners || []);
    } catch (err) {
        if (content) content.innerHTML =
            '<div style="padding:20px;text-align:center;color:#ef4444;font-size:12px;">⚠️ Failed to load. <button onclick="loadTournamentById(\'' + _esc(tid) + '\',true)" style="color:#f1c40f;background:none;border:none;cursor:pointer;font-weight:700;">Retry</button></div>';
    }
}

function _renderTournament(t, winners) {
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
                regDetails = `
                    <p style="font-size:13px;font-weight:800;color:#f1c40f;margin:4px 0 6px;">🛡️ Team: ${_esc(rd.team_name||'')}</p>
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
        if (reg && reg.registered) {
            html += `
            <div class="t-registered-badge" style="margin-top:10px;">
                <p style="font-size:13px;font-weight:800;color:#4ade80;margin:0 0 3px;">✅ You are registered!</p>
                <p style="font-size:11px;color:#64748b;margin:0;">Room ID & Password will be shared before match time.</p>
            </div>`;
        }

    } else if (t.status === 'match_live') {
        const reg = _tournamentRegCache[_selectedTid] || { registered: false };

        // ── Live banner
        html += `
        <div style="text-align:center;padding:16px;background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.28);border-radius:14px;margin-bottom:12px;">
            <span style="font-size:40px;display:block;margin-bottom:6px;" class="t-lock-icon">🔥</span>
            <p style="font-size:18px;font-weight:900;color:#f87171;margin:0 0 4px;letter-spacing:0.5px;">MATCH IS LIVE!</p>
            <p style="font-size:12px;color:#94a3b8;margin:0;">The battle has begun. Good luck to all players!</p>
        </div>`;

        if (reg && reg.registered) {
            // ── Room credentials dashboard (only for registered users)
            const roomId   = t.room_id       || null;
            const roomPass = t.room_password || null;

            html += `
            <div style="background:linear-gradient(135deg,rgba(241,196,15,0.08),rgba(251,146,60,0.06));border:1.5px solid rgba(241,196,15,0.35);border-radius:16px;padding:16px;margin-bottom:4px;">
                <p style="font-size:11px;font-weight:800;color:#f1c40f;text-transform:uppercase;letter-spacing:0.8px;margin:0 0 12px;text-align:center;">🔑 Your Room Details</p>

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
}

async function registerForTournament() {
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
    try {
        navigator.clipboard.writeText(adminUpi).then(() => {
            showToast('✅ UPI ID copied!', 'ok');
        }).catch(() => {
            // Fallback
            const el = document.createElement('textarea');
            el.value = adminUpi;
            document.body.appendChild(el);
            el.select();
            document.execCommand('copy');
            document.body.removeChild(el);
            showToast('✅ UPI ID copied!', 'ok');
        });
    } catch (e) {
        showToast('❌ Copy failed. Copy manually: ' + adminUpi, 'error');
    }
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
    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.openTelegramLink(botUrl);
    } else {
        window.open(botUrl, '_blank');
    }
    showToast('📤 Bot opened — send your screenshot!', 'ok');
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
