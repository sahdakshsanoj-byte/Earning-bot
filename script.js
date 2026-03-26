    // 1. Telegram WebApp Setup
const tg = window.Telegram.WebApp;
tg.expand();

// 2. Initial Setup & URL Data Fetching
const queryParams = new URLSearchParams(window.location.search);
const serverCoins = queryParams.get('coins');
const serverAds = queryParams.get('ads') || 0; // Bot se aaya 0/5 count
const refListStr = queryParams.get('ref_list');
const topUsersStr = queryParams.get('top_users');

if (serverCoins !== null) {
    localStorage.setItem('user_coins', serverCoins);
    localStorage.setItem('daily_ads_count', serverAds);
}

function updateDisplay() {
    let currentBalance = localStorage.getItem('user_coins') || 0;
    let adsDone = localStorage.getItem('daily_ads_count') || 0;
    
    // Balance Update
    const balanceEl = document.getElementById('balance');
    if (balanceEl) balanceEl.innerText = currentBalance + " 🪙";

    // Ads Progress Update
    const adText = document.getElementById('ad-count-text');
    const adProgress = document.getElementById('ad-progress');
    if (adText && adProgress) {
        adText.innerText = `${adsDone}/5`;
        adProgress.style.width = (adsDone * 20) + "%";
    }

    renderHistory();
    renderReferrals();
    renderLeaderboard();
}

// --- NAYE FUNCTIONS: ADS & SPONSORS ---

function watchAd() {
    let adsDone = parseInt(localStorage.getItem('daily_ads_count')) || 0;
    
    if (adsDone >= 5) {
        tg.showAlert("❌ Daily limit reached (5/5). Kal phir aana! ✨");
        return;
    }

    tg.showAlert("Opening Ad... 📺\nAd dekhne ke baad 'Back' dabayein!");
    
    // Yahan apna Telegram Ad link dalo
    window.open("https://t.me/tads_bot", "_blank"); 

    // Bot ko signal bhejo coins add karne ke liye
    tg.sendData(JSON.stringify({ type: "ad_click" }));
    
    // Local update (for instant feel)
    localStorage.setItem('daily_ads_count', adsDone + 1);
    let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
    localStorage.setItem('user_coins', currentCoins + 10);
    
    updateDisplay();
}

function checkSponsor(channelUsername) {
    tg.showAlert(`Checking membership for ${channelUsername}... 🔍`);
    
    // Bot ko signal bhejo membership verify karne ke liye
    tg.sendData(JSON.stringify({ 
        type: "check_sponsor", 
        channel_id: channelUsername 
    }));
}

// --- PURANE FUNCTIONS (As it is) ---

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

function openTask(link) {
    tg.showAlert("Opening Task... Secret Code dhoondhein! 🔍");
    window.open(link, '_blank');
}

function verifyTask(taskId, inputId, reward) {
    const userCode = document.getElementById(inputId).value.trim();
    const lastClaimKey = `last_claim_${taskId}`;
    const lastClaimTime = localStorage.getItem(lastClaimKey);
    const currentTime = new Date().getTime();
    
    const lockHours = 48;
    const lockMs = lockHours * 60 * 60 * 1000;

    if (lastClaimTime && (currentTime - lastClaimTime < lockMs)) {
        tg.showAlert(`✋ Wait! Ghante baaki hain.`);
        return;
    }

    // Example Codes
    const TASK_CODES = {'yt1':'YT786', 'yt2':'YT555', 'yt3':'YT999', 'web1':'WEB10', 'web2':'WEB20', 'web3':'WEB30'};

    if (userCode === TASK_CODES[taskId]) {
        let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
        localStorage.setItem('user_coins', currentCoins + reward);
        localStorage.setItem(lastClaimKey, currentTime);
        updateDisplay();
        tg.sendData(JSON.stringify({ type: "claim_bonus", amount: reward }));
        tg.showAlert(`Correct! +${reward} Coins ✅`);
        document.getElementById(inputId).value = ""; 
    } else {
        tg.showAlert("Wrong Secret Code! ❌");
    }
}

function renderLeaderboard() {
    const list = document.getElementById('leaderboard-list');
    if (!list || !topUsersStr || topUsersStr === "none") return;
    const users = topUsersStr.split('|');
    list.innerHTML = users.map((u, index) => {
        const [id, score] = u.split(':');
        let medal = index === 0 ? "🥇" : index === 1 ? "🥈" : index === 2 ? "🥉" : "👤";
        return `<div style="display:flex; justify-content:space-between; padding:12px; border-bottom:1px solid #1e293b;"><span>${medal} ${id}</span><span style="color:#f1c40f;">${score} 🪙</span></div>`;
    }).join('');
}

function renderReferrals() {
    const list = document.getElementById('refer-list');
    if (!list) return;
    if (!refListStr || refListStr === "none") {
        list.innerHTML = `<p style="text-align:center;">No referrals yet.</p>`;
        return;
    }
    const refs = refListStr.split(',');
    list.innerHTML = refs.map(id => `<div style="display:flex; justify-content:space-between; padding:10px; border-bottom:1px solid #1e293b;"><span>👤 ID: ${id}</span><span style="color:#2ecc71;">+50 ✅</span></div>`).join('');
}

function copyEmail() {
    navigator.clipboard.writeText("codetearn.help@gmail.com").then(() => {
        const status = document.getElementById('copy-status');
        if (status) { status.style.display = 'block'; setTimeout(() => { status.style.display = 'none'; }, 2000); }
        if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
    });
}

function requestWithdraw() {
    let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
    const upiId = document.getElementById('upi-id').value.trim();
    if (currentCoins < 1000) return tg.showAlert("Min 1000 Coins! ❌");
    if (!upiId.includes('@')) return tg.showAlert("Invalid UPI ID! 🏦");

    let history = JSON.parse(localStorage.getItem('withdraw_history')) || [];
    history.unshift({ date: new Date().toLocaleDateString(), amount: currentCoins, status: "Pending ⏳" });
    localStorage.setItem('withdraw_history', JSON.stringify(history));

    tg.sendData(JSON.stringify({ type: "withdraw_request", amount: currentCoins, upi: upiId }));
    localStorage.setItem('user_coins', 0);
    updateDisplay();
    tg.showAlert("Request Sent! ✅");
}

function renderHistory() {
    let history = JSON.parse(localStorage.getItem('withdraw_history')) || [];
    const list = document.getElementById('history-list');
    if (!list) return;
    list.innerHTML = history.length === 0 ? `<p style="text-align:center;">No history.</p>` : 
    history.map(item => `<div style="display:flex; justify-content:space-between; padding:10px; background:#0f172a; margin-bottom:5px; border-radius:5px;"><span>📅 ${item.date}</span><span>💰 ${item.amount}</span><span style="color:#f1c40f;">${item.status}</span></div>`).join('');
}

function inviteFriend() {
    const userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "guest";
    const link = `https://t.me/Cdotern_bot?start=${userId}`;
    navigator.clipboard.writeText(link).then(() => tg.showAlert("Link Copied! 🚀"));
}

function claimDaily() {
    let lastClaim = localStorage.getItem('last_claim_daily');
    let today = new Date().toDateString();
    if (lastClaim === today) return tg.showAlert("Already claimed! ✨");
    let coins = parseInt(localStorage.getItem('user_coins')) || 0;
    localStorage.setItem('user_coins', coins + 10);
    localStorage.setItem('last_claim_daily', today);
    updateDisplay();
    tg.sendData(JSON.stringify({ type: "claim_bonus", amount: 10 }));
    tg.showAlert("10 Coins added! 🎁");
}

updateDisplay();
        
