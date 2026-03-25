    // 1. Telegram WebApp Setup
const tg = window.Telegram.WebApp;
tg.expand();

// 2. Initial Setup & URL Data Fetching
const queryParams = new URLSearchParams(window.location.search);
const serverCoins = queryParams.get('coins');
const refListStr = queryParams.get('ref_list'); // Bot se aayi referral list
const topUsersStr = queryParams.get('top_users'); // Bot se aaya leaderboard data

if (serverCoins !== null) {
    localStorage.setItem('user_coins', serverCoins);
}

function updateDisplay() {
    let currentBalance = localStorage.getItem('user_coins') || 0;
    const balanceEl = document.getElementById('balance');
    if (balanceEl) balanceEl.innerText = currentBalance + " 🪙";
    renderHistory();
    renderReferrals();
    renderLeaderboard();
}

// 3. Tab Switching
function switchTab(tabId, el) {
    const tabs = document.querySelectorAll('.tab-content');
    tabs.forEach(tab => {
        tab.style.display = 'none';
        tab.classList.remove('active-tab');
    });

    const buttons = document.querySelectorAll('.nav-item');
    buttons.forEach(btn => btn.classList.remove('active'));

    const target = document.getElementById(tabId);
    if (target) {
        target.style.display = 'block';
        target.classList.add('active-tab');
        if (el && el.classList) el.classList.add('active');
        document.getElementById('tab-title').innerText = tabId.charAt(0).toUpperCase() + tabId.slice(1);
        
        if(tabId === 'refer') {
            const userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "guest";
            const inviteLink = `https://t.me/Cdotern_bot?start=${userId}`;
            document.getElementById('display-link').innerText = inviteLink;
        }
    }
}

// 4. Task Verification (config.js based)
function openTask(link) {
    tg.showAlert("Opening Task... Video/Site se Secret Code dhoondhein! 🔍");
    window.open(link, '_blank');
}

function verifyTask(taskId, inputId, reward) {
    const userCode = document.getElementById(inputId).value.trim();
    const lastClaimKey = `last_claim_${taskId}`;
    const lastClaimTime = localStorage.getItem(lastClaimKey);
    const currentTime = new Date().getTime();
    
    // config.js se time aur codes uthana
    const lockHours = (typeof APP_CONFIG !== 'undefined') ? APP_CONFIG.lock_time_hours : 48;
    const lockMs = lockHours * 60 * 60 * 1000;

    if (lastClaimTime && (currentTime - lastClaimTime < lockMs)) {
        const remainingHours = Math.ceil((lockMs - (currentTime - lastClaimTime)) / (1000 * 60 * 60));
        tg.showAlert(`✋ Thoda wait! Is task ke liye ${remainingHours} ghante baaki hain.`);
        return;
    }

    // Default Codes if config fails
    const TASK_CODES = (typeof APP_CONFIG !== 'undefined') ? 
        Object.fromEntries([...APP_CONFIG.youtube_tasks, ...APP_CONFIG.website_tasks].map(t => [t.id, t.code])) :
        {'yt1':'YT786', 'yt2':'YT555', 'yt3':'YT999', 'web1':'WEB10', 'web2':'WEB20', 'web3':'WEB30'};

    if (userCode === TASK_CODES[taskId]) {
        let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
        localStorage.setItem('user_coins', currentCoins + reward);
        localStorage.setItem(lastClaimKey, currentTime);
        updateDisplay();
        tg.sendData(JSON.stringify({ type: "claim_bonus", amount: reward }));
        tg.showAlert(`Correct! +${reward} Coins added. ✅`);
        document.getElementById(inputId).value = ""; 
    } else {
        tg.showAlert("Wrong Secret Code! ❌");
    }
}

// 5. Leaderboard & Referral Rendering
function renderLeaderboard() {
    const list = document.getElementById('leaderboard-list');
    if (!list || !topUsersStr || topUsersStr === "none") return;

    const users = topUsersStr.split('|'); // Bot should send like "ID:Coins|ID:Coins"
    list.innerHTML = users.map((u, index) => {
        const [id, score] = u.split(':');
        let medal = index === 0 ? "🥇" : index === 1 ? "🥈" : index === 2 ? "🥉" : "👤";
        return `
            <div style="display:flex; justify-content:space-between; padding:12px; border-bottom:1px solid #1e293b; background:${index < 3 ? 'rgba(241, 196, 15, 0.1)' : 'transparent'};">
                <span>${medal} ${id}</span>
                <span style="color:var(--gold); font-weight:bold;">${score} 🪙</span>
            </div>`;
    }).join('');
}

function renderReferrals() {
    const list = document.getElementById('refer-list');
    if (!list) return;
    if (!refListStr || refListStr === "none") {
        list.innerHTML = `<p style="text-align:center; color:#94a3b8;">No referrals yet. Share link! 🚀</p>`;
        return;
    }
    const refs = refListStr.split(',');
    list.innerHTML = refs.map(id => `
        <div style="display:flex; justify-content:space-between; padding:10px; border-bottom:1px solid #1e293b;">
            <span>👤 User ID: ${id}</span>
            <span style="color:#2ecc71;">+50 ✅</span>
        </div>`).join('');
}

// 6. Email Copy System (Haptic Feedback Included)
function copyEmail() {
    const email = (typeof APP_CONFIG !== 'undefined') ? APP_CONFIG.support_email : "codetearn.help@gmail.com";
    
    navigator.clipboard.writeText(email).then(() => {
        const status = document.getElementById('copy-status');
        if (status) {
            status.style.display = 'block';
            setTimeout(() => { status.style.display = 'none'; }, 2000);
        }
        // Vibrate Phone
        if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
    });
}

// 7. Withdraw & Support
function requestWithdraw() {
    let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
    const upiId = document.getElementById('upi-id').value.trim();
    const minLimit = (typeof APP_CONFIG !== 'undefined') ? APP_CONFIG.min_withdraw : 1000;

    if (currentCoins < minLimit) {
        tg.showAlert(`Min ${minLimit} Coins required! ❌`);
        return;
    }
    if (!upiId.includes('@')) {
        tg.showAlert("Invalid UPI ID! 🏦");
        return;
    }

    let history = JSON.parse(localStorage.getItem('withdraw_history')) || [];
    history.unshift({ date: new Date().toLocaleDateString(), amount: currentCoins, status: "Pending ⏳" });
    localStorage.setItem('withdraw_history', JSON.stringify(history));

    tg.sendData(JSON.stringify({ type: "withdraw_request", amount: currentCoins, upi: upiId }));
    localStorage.setItem('user_coins', 0);
    updateDisplay();
    tg.showAlert("Request Sent! Check History. ✅");
}

function renderHistory() {
    let history = JSON.parse(localStorage.getItem('withdraw_history')) || [];
    const list = document.getElementById('history-list');
    if (!list) return;
    list.innerHTML = history.length === 0 ? `<p style="text-align:center; color:#94a3b8;">No history yet.</p>` : 
    history.map(item => `<div style="display:flex; justify-content:space-between; padding:10px; border-bottom:1px solid #1e293b; background:#0f172a; margin-bottom:5px; border-radius:5px;"><span>📅 ${item.date}</span><span>💰 ${item.amount}</span><span style="color:#f1c40f;">${item.status}</span></div>`).join('');
}

function sendSupport() {
    const msg = document.getElementById('support-msg').value;
    if(!msg.trim()) return tg.showAlert("Please write something.");
    tg.sendData(JSON.stringify({type: 'support', message: msg}));
    tg.showAlert("Sent to Admin! ✅");
    document.getElementById('support-msg').value = "";
}

function inviteFriend() {
    const userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "guest";
    const link = `https://t.me/Codetearn_bot?start=${userId}`;
    navigator.clipboard.writeText(link).then(() => tg.showAlert("Link Copied! 🚀"));
}

function claimDaily() {
    let lastClaim = localStorage.getItem('last_claim_daily');
    let today = new Date().toDateString();
    if (lastClaim === today) { return tg.showAlert("Aaj ka bonus mil gaya! Kal aana. ✨"); }
    let coins = parseInt(localStorage.getItem('user_coins')) || 0;
    localStorage.setItem('user_coins', coins + 10);
    localStorage.setItem('last_claim_daily', today);
    updateDisplay();
    tg.sendData(JSON.stringify({ type: "claim_bonus", amount: 10 }));
    tg.showAlert("10 Coins added! 🎁");
}

// Initial Call
updateDisplay();
