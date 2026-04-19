// ============================================================
// SCRIPT.JS — Daksh Grand Earn (Secure & Clean)
// ============================================================

const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();
tg.enableClosingConfirmation();

const userId = tg.initDataUnsafe?.user?.id
    || new URLSearchParams(window.location.search).get('user_id');

let userData = {};

// Tracks in-flight requests to prevent duplicate clicks
const _pendingRequests = new Set();
let monetagSdkPromise = null;
let monetagPreloaded = false;

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
// COUNTDOWN HELPER — calls updateFn(s) every tick, doneFn() at 0
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

            const coinsPct = Math.min((coins / 4000) * 100, 100);
            const refPct   = Math.min((refCount / 5) * 100, 100);

            const coinsBar  = document.getElementById('coins-progress-bar');
            const refBar    = document.getElementById('ref-progress-bar');
            const coinsText = document.getElementById('coins-progress-text');
            const refText   = document.getElementById('ref-progress-text');

            if (coinsBar) {
                coinsBar.style.width      = coinsPct + '%';
                coinsBar.style.background = coins >= 4000
                    ? 'linear-gradient(90deg,#2ecc71,#27ae60)'
                    : 'linear-gradient(90deg,#f1c40f,#f39c12)';
            }
            if (refBar)    refBar.style.width = refPct + '%';
            if (coinsText) coinsText.innerText = `${coins} / 4000 ${coins >= 4000 ? '✅' : ''}`;
            if (refText)   refText.innerText   = `${refCount} / 5 ${refCount >= 5 ? '✅' : ''}`;

            // Leaderboard — update only if we have fresh data
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
// LEADERBOARD AUTO-REFRESH (every 10 minutes)
// ============================================================
async function refreshLeaderboard() {
    try {
        const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/get_leaderboard`);
        const data = await res.json();
        if (data.status === "success" && data.leaderboard) {
            updateLeaderboardUI(data.leaderboard);
        }
    } catch (e) { /* Silent — leaderboard refresh is non-critical */ }
}

// ============================================================
// DAILY BONUS — 10s countdown then API call
// ============================================================
function checkDailyBonus(lastClaimTs) {
    const btn = document.getElementById('daily-btn');
    if (!btn) return;
    if (!lastClaimTs) {
        btn.disabled  = false;
        btn.innerText = "Claim Now";
        return;
    }
    try {
        const diffHours = (new Date() - new Date(lastClaimTs)) / 3600000;
        if (diffHours < 24) {
            const remaining = 24 - diffHours;
            const h = Math.floor(remaining);
            const m = Math.floor((remaining - h) * 60);
            btn.disabled  = true;
            btn.innerText = `✅ ${h}h ${m}m left`;
        } else {
            btn.disabled  = false;
            btn.innerText = "Claim Now";
        }
    } catch (e) {
        btn.disabled  = false;
        btn.innerText = "Claim Now";
    }
}

async function claimDaily() {
    if (!userId) return;
    if (_pendingRequests.has('claimDaily')) return;
    _pendingRequests.add('claimDaily');

    const btn = document.getElementById('daily-btn');
    if (btn) btn.disabled = true;

    startCountdown(10,
        (s) => { if (btn) btn.innerText = `Crediting in ${s}s...`; },
        async () => {
            try {
                const res  = await fetchWithRetry(
                    `${CONFIG.API_BASE_URL}/claim_daily/${userId}`,
                    { method: 'POST' }
                );
                const data = await res.json();
                if (data.status === "success") {
                    showToast("🎁 10 coins added to your balance!", "success");
                    fetchLiveData();
                } else {
                    showToast(data.message, "error");
                    if (btn) { btn.disabled = false; btn.innerText = "Claim Now"; }
                }
            } catch (e) {
                showToast("⚠️ Error! Please retry.", "error");
                if (btn) { btn.disabled = false; btn.innerText = "Claim Now"; }
            } finally {
                _pendingRequests.delete('claimDaily');
            }
        }
    );
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

    if (!rawAmount)              return showToast("Please enter the coin amount!", "error");
    if (isNaN(reqAmount))        return showToast("Please enter a valid number!", "error");
    if (reqAmount <= 0)          return showToast("Amount cannot be zero or negative!", "error");
    if (reqAmount < 4000)        return showToast(`Minimum 4000 coins required.`, "error");
    if (reqAmount > totalCoins)  return showToast(`Insufficient balance. You have ${totalCoins} coins.`, "error");
    if (!upi || !upi.includes('@')) return showToast("Please enter a valid UPI ID! (Example: name@upi)", "error");

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

async function verifyTask(taskId, inputId) {
    const code = document.getElementById(inputId)?.value.trim();
    if (!code) return showToast("Please enter the code!", "error");

    const reqKey    = `verify_${taskId}`;
    if (_pendingRequests.has(reqKey)) return;
    _pendingRequests.add(reqKey);

    const verifyBtn = document.querySelector(`[onclick="verifyTask('${taskId}', '${inputId}')"]`);
    if (verifyBtn) verifyBtn.disabled = true;

    startCountdown(10,
        (s) => { if (verifyBtn) verifyBtn.innerText = `Wait ${s}s...`; },
        async () => {
            try {
                const res  = await fetchWithRetry(`${CONFIG.API_BASE_URL}/verify_task`, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ user_id: userId, task_id: taskId, code: code })
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
// CHANNEL JOIN — 15s countdown, 3-retry backend, retry on fail
// ============================================================
function updateChannelButtons(channelClaims) {
    // Regular channels — one-time claim
    ['official', 'channel2', 'channel3'].forEach(ch => {
        const btn = document.getElementById(`ch-btn-${ch}`);
        if (!btn) return;
        if (channelClaims[ch]) {
            btn.disabled         = true;
            btn.innerText        = "✅ Joined";
            btn.style.background = "#2ecc71";
        }
    });

    // Slot 1 — link-based claim (reclaim allowed if link changes)
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

    // Slot 2 — link-based claim
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

// Silent click tracker — fire and forget
function trackSponsorClick(slotId, linkUrl) {
    if (!userId || !linkUrl) return;
    fetch(`${CONFIG.API_BASE_URL}/click_sponsor`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ user_id: userId, slot_id: slotId, link_url: linkUrl })
    }).catch(() => {});
}

// ============================================================
// CHANNEL CLAIM — Fixed: 15s countdown, backend retries 3x,
// Retry button re-opens the channel so user can join again
// ============================================================
async function claimChannel(channelId, channelUrl) {
    if (!userId) return showToast("User ID not found!", "error");

    const reqKey = `channel_${channelId}`;
    if (_pendingRequests.has(reqKey)) return;

    // Track unique click for sponsor slots
    if (channelId === 'slot1' || channelId === 'slot2' || channelId === 'slot3') {
        trackSponsorClick(channelId, channelUrl);
    }

    // Open the channel link first
    window.open(channelUrl, '_blank');

    _pendingRequests.add(reqKey);
    const btn = document.getElementById(`ch-btn-${channelId}`);
    if (btn) btn.disabled = true;

    // 15 seconds gives user time to join + Telegram API time to update
    startCountdown(15,
        (s) => { if (btn) btn.innerText = `Join & wait ${s}s...`; },
        async () => {
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
                    // Backend tried 3 times — user may not have joined yet
                    showToast("❌ Join not confirmed! Make sure you joined, then tap Retry.", "error");
                    if (btn) {
                        btn.disabled         = false;
                        btn.innerText        = "🔄 Retry";
                        btn.style.background = "#e74c3c";
                        // Retry opens channel again so user can join, then retries claim
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
// AD COUNTER
// ============================================================
function updateAdCounter(adsToday, adsDate) {
    const container = document.getElementById('ad-reward-container') || document.getElementById('ad-container');
    const today     = new Date().toISOString().split('T')[0];
    const done      = (adsDate === today) ? adsToday : 0;
    const counterEl = document.getElementById('ad-counter');
    if (counterEl) counterEl.innerText = `${done}/5`;
    if (done >= 5 && container) {
        container.innerHTML = `<div style="text-align:center;padding:10px 0;color:#64748b;font-size:13px;">✅ All 5 ads completed for today! Come back tomorrow.</div>`;
    }
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
                <span style="margin-left:auto;font-size:12px;color:#2ecc71;font-weight:700;">+50 🪙</span>
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
// MONETAG REWARDED AD — claim coins after completed ad
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

        if (adResult?.reward_event_type && adResult.reward_event_type !== 'valued') {
            showToast("Ad was skipped. Watch the full ad to earn coins.", "error");
            preloadMonetagAd();
            return;
        }

        if (btn) btn.innerText = "Crediting...";
        const res = await fetchWithRetry(
            `${CONFIG.API_BASE_URL}/claim_ad/${userId}`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: tokenData.token })
            }
        );
        const data = await res.json();
        const adsDone = data.ads_done || data.data?.ads_done;

        if (data.status === "success") {
            showToast(`✅ ${data.message}`, "success");
            const counterEl = document.getElementById('ad-counter');
            if (counterEl && adsDone !== undefined) counterEl.innerText = `${adsDone}/5`;
            fetchLiveData();
        } else {
            showToast(data.message || "Unable to claim ad reward.", "error");
        }
    } catch (e) {
        showToast("Ad not completed. No coins awarded.", "error");
    } finally {
        _pendingRequests.delete('showAd');
        if (btn) { btn.disabled = false; btn.innerText = "Watch Ad & Earn"; }
        preloadMonetagAd();
    }
}

// ============================================================
// DEVICE CHECK
// ============================================================
async function generateFingerprint() {
    try {
        const data = [
            navigator.userAgent, navigator.language,
            screen.width + "x" + screen.height, screen.colorDepth,
            new Date().getTimezoneOffset(),
            navigator.hardwareConcurrency || "",
            navigator.platform || ""
        ].join("|");
        const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(data));
        return Array.from(new Uint8Array(buf))
            .map(b => b.toString(16).padStart(2, "0")).join("");
    } catch (e) { return ""; }
}

async function checkDevice() {
    if (!userId) return;
    try {
        const fingerprint = await generateFingerprint();
        const res  = await fetch(`${CONFIG.API_BASE_URL}/check_device`, {
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
// UTILITY FUNCTIONS
// ============================================================

/**
 * Called when the backend confirms a user is blocked.
 * Hides the full app and shows only the Support/Help tab
 * so the blocked user can still contact the admin.
 */
function showBlockedView() {
    // Hide every tab-content
    document.querySelectorAll('.tab-content').forEach(el => {
        el.style.display = 'none';
        el.classList.remove('active-tab');
    });

    // Hide the bottom navigation bar entirely
    const nav = document.querySelector('.bottom-nav');
    if (nav) nav.style.display = 'none';

    // Show the help/support tab
    const helpTab = document.getElementById('help');
    if (helpTab) {
        helpTab.style.display = 'block';
        helpTab.classList.add('active-tab');
    }

    // Show the blocked banner inside the help tab
    const banner = document.getElementById('blocked-banner');
    if (banner) banner.style.display = 'block';

    // Update page title
    const titleEl = document.getElementById('tab-title');
    if (titleEl) titleEl.textContent = '🚫 Account Blocked';
}

function copyEmail() {
    navigator.clipboard.writeText('cdotern.help@gmail.com').catch(() => {});
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
            navigator.share({ text: `${shareText}
${link}` }).catch(() => {});
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
    if (el && el.classList.contains('nav-item')) el.classList.add('active');

    const titles = {
        rewards:     'Rewards',
        tasks:       'Tasks',
        leaderboard: 'Leaderboard',
        refer:       'Refer & Earn',
        history:     'History',
        help:        'Support'
    };
    const titleEl = document.getElementById('tab-title');
    if (titleEl) titleEl.innerText = titles[tabId] || tabId;

    if (tabId === 'history') loadHistory();
}

// ============================================================
// SPONSOR SLOTS — Auto unlock from CONFIG.SPONSORS
// Slot 1 and Slot 2 use separate channel IDs (slot1, slot2)
// so they are completely independent from each other.
// Lock animation is NOT changed.
// ============================================================
function initSlots() {
    const s = CONFIG.SPONSORS;
    if (!s) return;

    // ── Slot 1 ──
    if (s.slot1?.active) {
        const el = document.getElementById('sponsor-slot-1');
        if (!el) return;
        const overlay = el.querySelector('.lock-overlay');
        if (overlay) overlay.remove();

        const btn = el.querySelector('button');
        if (btn) {
            btn.id            = 'ch-btn-slot1';
            btn.disabled      = false;
            btn.style.opacity = '1';
            btn.style.background = '#38bdf8';
            btn.style.color      = '#000';
            btn.style.fontWeight = '700';
            btn.textContent   = 'Join & Claim';
            btn.onclick       = () => claimChannel('slot1', s.slot1.link);
        }

        const ps = el.querySelectorAll('p');
        if (ps[0] && s.slot1.name) ps[0].textContent = s.slot1.name;
        if (ps[1] && s.slot1.desc) ps[1].textContent = s.slot1.desc;
    }

    // ── Slot 2 ──
    if (s.slot2?.active) {
        const el = document.getElementById('sponsor-slot-2');
        if (!el) return;
        const overlay = el.querySelector('.lock-overlay');
        if (overlay) overlay.remove();

        const oldBtn = document.getElementById('ch-btn-sponsor1');
        if (oldBtn) {
            oldBtn.id            = 'ch-btn-slot2';
            oldBtn.disabled      = false;
            oldBtn.style.opacity = '1';
            oldBtn.textContent   = 'Join & Claim';
            oldBtn.onclick       = () => claimChannel('slot2', s.slot2.link);
        }
    }

    // ── Slot 3 ── (partner task — unchanged behaviour)
    if (s.slot3?.active) {
        const el = document.getElementById('sponsor-slot-3');
        if (!el) return;
        const overlay = el.querySelector('.lock-overlay');
        if (overlay) overlay.remove();
        el.querySelectorAll('button').forEach(b => {
            b.disabled = false;
            b.style.opacity = '1';
        });
        el.querySelectorAll('input').forEach(i => {
            i.disabled = false;
        });
        const openBtn = el.querySelector('button:first-of-type');
        if (openBtn) openBtn.onclick = () => window.open(s.slot3.link, '_blank');
    }
}

// ============================================================
// INIT — Slots first so button IDs exist before updateChannelButtons
// ============================================================
initSlots();
checkDevice();
fetchLiveData();
preloadMonetagAd();

// Leaderboard auto-refresh every 10 minutes
setInterval(refreshLeaderboard, 10 * 60 * 1000);
