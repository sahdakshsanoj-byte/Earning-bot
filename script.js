// ============================================================
// SCRIPT.JS — Complete Frontend Logic
// ============================================================

const tg = window.Telegram.WebApp;
tg.expand();
tg.enableClosingConfirmation();

// User ID safely fetch karo — pehle Telegram se, phir URL se
const userId = tg.initDataUnsafe?.user?.id || new URLSearchParams(window.location.search).get('user_id');
let userData = {};

// ---- TOAST NOTIFICATION ----
function showToast(msg, type = "info") {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.className = `show ${type}`;
    setTimeout(() => { toast.className = ''; }, 3000);
}

// ---- DATA FETCH ----
async function fetchLiveData() {
    if (!userId) {
        document.getElementById('balance').innerText = "ID Error";
        return;
    }
    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/get_user/${userId}`);
        const data = await res.json();

        if (data.status === "success") {
            userData = data;

            // Balance update
            document.getElementById('balance').innerText = `${data.coins} 🪙`;

            // Withdraw Progress Bars
            const coins = data.coins || 0;
            const refCount = (data.referrals || []).length;

            const coinsPct = Math.min((coins / 4000) * 100, 100);
            const refPct = Math.min((refCount / 5) * 100, 100);

            const coinsBar = document.getElementById('coins-progress-bar');
            const refBar = document.getElementById('ref-progress-bar');
            const coinsText = document.getElementById('coins-progress-text');
            const refText = document.getElementById('ref-progress-text');

            if (coinsBar) {
                coinsBar.style.width = coinsPct + '%';
                coinsBar.style.background = coins >= 4000
                    ? 'linear-gradient(90deg,#2ecc71,#27ae60)'
                    : 'linear-gradient(90deg,#f1c40f,#f39c12)';
            }
            if (refBar) {
                refBar.style.width = refPct + '%';
            }
            if (coinsText) coinsText.innerText = `${coins} / 4000 ${coins >= 4000 ? '✅' : ''}`;
            if (refText) refText.innerText = `${refCount} / 5 ${refCount >= 5 ? '✅' : ''}`;

            // Leaderboard
            updateLeaderboardUI(data.leaderboard);

            // Referral link
            document.getElementById('display-link').innerText =
                `https://t.me/${CONFIG.BOT_USERNAME}?start=${userId}`;

            // Referrals list
            updateReferralList(data.referrals);

            // Task completion status
            applyCompletedTasks(data.completed_tasks || []);

            // Daily bonus button status
            checkDailyBonus(data.last_claim);
        }
    } catch (err) {
        console.error("API Error:", err);
        showToast("Connection error. Retry...", "error");
    }
}

// ---- DAILY BONUS CHECK ----
function checkDailyBonus(lastClaim) {
    const btn = document.querySelector('#daily-btn');
    if (!btn) return;
    const today = new Date().toISOString().split('T')[0]; // YYYY-MM-DD
    if (lastClaim === today) {
        btn.disabled = true;
        btn.innerText = "✅ Claimed Today";
    } else {
        btn.disabled = false;
        btn.innerText = "Claim Now";
    }
}

// ---- CLAIM DAILY (API se — App close nahi hoga) ----
async function claimDaily() {
    if (!userId) return;
    const btn = document.getElementById('daily-btn');
    btn.disabled = true;
    btn.innerText = "Claiming...";
    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/claim_daily/${userId}`, { method: 'POST' });
        const data = await res.json();
        if (data.status === "success") {
            showToast("🎁 10 coins claim ho gaye!", "success");
            fetchLiveData();
        } else {
            showToast(data.message, "error");
            btn.disabled = false;
            btn.innerText = "Claim Now";
        }
    } catch (e) {
        showToast("Error! Retry.", "error");
        btn.disabled = false;
        btn.innerText = "Claim Now";
    }
}

// ---- WITHDRAW (API se) ----
async function requestWithdraw() {
    const upi = document.getElementById('upi-id').value.trim();
    const coins = userData.coins || 0;

    if (coins < 1000) return showToast(`Need 1000 coins. You have ${coins}.`, "error");
    if (!upi.includes('@')) return showToast("Valid UPI ID enter karo!", "error");

    const btn = document.querySelector('.withdraw-btn');
    btn.disabled = true;
    btn.innerText = "Processing...";

    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/withdraw`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, upi_id: upi })
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast("💸 Withdrawal request submitted!", "success");
            document.getElementById('upi-id').value = '';
            fetchLiveData();
        } else {
            showToast(data.message, "error");
        }
    } catch (e) {
        showToast("Error! Retry.", "error");
    }
    btn.disabled = false;
    btn.innerText = "Withdraw Now";
}

// ---- TASK OPEN ----
function openTask(taskKey, type) {
    const link = type === 'yt' ? CONFIG.YT_LINKS[taskKey] : CONFIG.WEB_LINKS[taskKey];
    if (link && link !== '#') {
        window.open(link, '_blank');
    } else {
        showToast("Link update hoga jald!", "error");
    }
}

// ---- TASK VERIFY (API se) ----
async function verifyTask(taskId, inputId, reward) {
    const code = document.getElementById(inputId)?.value.trim();
    if (!code) return showToast("Code enter karo!", "error");

    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/verify_task`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, task_id: taskId, code: code, reward: reward })
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast(`✅ ${data.message}`, "success");
            fetchLiveData();
        } else {
            showToast(data.message, "error");
        }
    } catch (e) {
        showToast("Error! Retry.", "error");
    }
}

// ---- COMPLETED TASKS MARK ----
function applyCompletedTasks(completedList) {
    completedList.forEach(taskId => {
        const item = document.querySelector(`[data-task="${taskId}"]`);
        if (item) item.classList.add('done');
    });
}

// ---- LEADERBOARD UI ----
function updateLeaderboardUI(leaderboardData) {
    const list = document.getElementById('leaderboard-list');
    if (!list) return;
    if (!leaderboardData || leaderboardData === "none") {
        list.innerHTML = "<p class='spinner'>No users yet.</p>";
        return;
    }
    const medals = ['🥇', '🥈', '🥉'];
    const players = leaderboardData.split('|');
    let html = "";
    players.forEach((p, i) => {
        const [id, coins] = p.split(':');
        const isMe = String(id) === String(userId);
        html += `
            <div class="lb-item" style="${isMe ? 'background:rgba(99,102,241,0.1);border-radius:8px;padding:10px;' : ''}">
                <span class="lb-rank">${medals[i] || `#${i + 1}`}</span>
                <span class="lb-user">${isMe ? '👤 You' : `User ${id}`}</span>
                <span class="lb-coins">${coins} 🪙</span>
            </div>`;
    });
    list.innerHTML = html;
}

// ---- REFERRAL LIST ----
function updateReferralList(referrals) {
    const list = document.getElementById('refer-list');
    if (!list) return;
    if (!referrals || referrals === "none") {
        list.innerHTML = "<p style='color:#94a3b8;text-align:center;'>No referrals yet.</p>";
        return;
    }
    const refs = referrals.split(',');
    let html = "";
    refs.forEach((id, i) => {
        html += `<div class="refer-item">👤 Friend ${i + 1} — ID: ${id}</div>`;
    });
    list.innerHTML = html;
}

// ---- WITHDRAWAL HISTORY ----
async function loadHistory() {
    const list = document.getElementById('history-list');
    if (!list || !userId) return;
    list.innerHTML = "<p class='spinner'>Loading...</p>";
    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/get_history/${userId}`);
        const data = await res.json();
        if (data.history && data.history.length > 0) {
            let html = "";
            data.history.forEach(h => {
                html += `
                    <div class="history-item">
                        <div>💸 <b>${h.amount} coins</b> — UPI: ${h.upi_id}</div>
                        <div class="history-status" style="color:${h.status.includes('Approved') ? '#22c55e' : h.status.includes('Rejected') ? '#e74c3c' : '#f1c40f'}">
                            ${h.status} • ${h.date}
                        </div>
                    </div>`;
            });
            list.innerHTML = html;
        } else {
            list.innerHTML = "<p style='color:#94a3b8;text-align:center;'>No history found.</p>";
        }
    } catch (e) {
        list.innerHTML = "<p style='color:#94a3b8;text-align:center;'>Error loading history.</p>";
    }
}

// ---- SUPPORT ----
async function sendSupport() {
    const msg = document.getElementById('support-msg').value.trim();
    if (!msg) return showToast("Message likho!", "error");
    try {
        const res = await fetch(`${CONFIG.API_BASE_URL}/send_support`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, message: msg })
        });
        const data = await res.json();
        if (data.status === "success") {
            showToast("✅ Support message sent!", "success");
            document.getElementById('support-msg').value = '';
        } else {
            showToast("Error sending message.", "error");
        }
    } catch (e) {
        showToast("Error! Retry.", "error");
    }
}

// ---- COPY EMAIL ----
function copyEmail() {
    navigator.clipboard.writeText('codetearn.help@gmail.com');
    const status = document.getElementById('copy-status');
    status.style.display = 'block';
    setTimeout(() => { status.style.display = 'none'; }, 2000);
}

// ---- INVITE FRIEND ----
function inviteFriend() {
    const link = `https://t.me/${CONFIG.BOT_USERNAME}?start=${userId}`;
    const shareText = `Join Daksh Grand Earn aur coins kamao! 🚀\n${link}`;
    if (navigator.share) {
        navigator.share({ text: shareText });
    } else {
        navigator.clipboard.writeText(link);
        showToast("✅ Link copied!", "success");
    }
}

// ---- TAB SWITCHING ----
function switchTab(tabId, el) {
    document.querySelectorAll('.tab-content').forEach(t => {
        t.style.display = 'none';
        t.classList.remove('active-tab');
    });
    const tab = document.getElementById(tabId);
    if (tab) { tab.style.display = 'block'; tab.classList.add('active-tab'); }

    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    if (el && el.classList.contains('nav-item')) el.classList.add('active');

    const titles = { rewards: 'Rewards', tasks: 'Tasks', leaderboard: 'Leaderboard', refer: 'Refer & Earn', history: 'History', help: 'Support' };
    document.getElementById('tab-title').innerText = titles[tabId] || tabId;

    if (tabId === 'history') loadHistory();
}

// ============================================================
// ADSGRAM — Watch Ad & Earn 5 Coins
// ============================================================

let AdController = null;

async function initAdsgram() {
    try {
        if (window.Adsgram) {
            AdController = window.Adsgram.init({
                blockId: CONFIG.ADSGRAM_BLOCK_ID
            });
        }
    } catch (e) {
        console.log("Adsgram not available:", e);
    }
}

async function showAd() {
    if (!AdController) {
        showToast("Ad abhi available nahi hai.", "error");
        return;
    }
    try {
        await AdController.show();
        const res = await fetch(`${CONFIG.API_BASE_URL}/watch_ad/${userId}`, { method: 'POST' });
        const data = await res.json();
        if (data.status === "success") {
            showToast("✅ 5 coins mil gaye!", "success");
            fetchLiveData();
        }
    } catch (e) {
        showToast("Ad skip kiya — koi coins nahi.", "error");
    }
}

// ============================================================
// FINGERPRINT + IP — Device Check (Anti Multi-Account)
// ============================================================

async function generateFingerprint() {
    const data = [
        navigator.userAgent,
        navigator.language,
        screen.width + "x" + screen.height,
        screen.colorDepth,
        new Date().getTimezoneOffset(),
        navigator.hardwareConcurrency || "",
        navigator.platform || ""
    ].join("|");

    const encoder = new TextEncoder();
    const buf = await crypto.subtle.digest("SHA-256", encoder.encode(data));
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
}

async function checkDevice() {
    if (!userId) return;
    try {
        const fingerprint = await generateFingerprint();
        const res = await fetch(`${CONFIG.API_BASE_URL}/check_device`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, fingerprint: fingerprint })
        });
        const data = await res.json();
        if (data.status === "blocked") {
            document.body.innerHTML = `
                <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; background:#0f172a; color:#e2e8f0; text-align:center; padding:20px;">
                    <div style="font-size:60px;">🚫</div>
                    <h2 style="color:#e74c3c; margin:15px 0;">Account Blocked</h2>
                    <p style="color:#94a3b8; font-size:14px;">Is device par ek account already registered hai.<br>Multiple accounts allowed nahi hain.</p>
                </div>`;
        }
    } catch (e) {
        console.log("Device check error:", e);
    }
}

// ============================================================
// INIT
// ============================================================

checkDevice();
fetchLiveData();
initAdsgram();
setInterval(fetchLiveData, 10000);
