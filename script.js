// 1. Telegram WebApp Setup
const tg = window.Telegram.WebApp;
tg.expand();

// 2. Initial Setup & Display
const queryParams = new URLSearchParams(window.location.search);
const serverCoins = queryParams.get('coins');
if (serverCoins !== null) {
    localStorage.setItem('user_coins', serverCoins);
}

function updateDisplay() {
    let currentBalance = localStorage.getItem('user_coins') || 0;
    const balanceEl = document.getElementById('balance');
    if (balanceEl) balanceEl.innerText = currentBalance + " 🪙";
    renderHistory();
}
updateDisplay();

// 3. Tab Switching Function
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
            const inviteLink = `https://t.me/Codetearn_bot?start=${userId}`;
            document.getElementById('display-link').innerText = inviteLink;
        }
    }
}

// 4. Multi-Task Secret Code Logic (3 YT + 3 WEB)
function openTask(link) {
    tg.showAlert("Opening Task... Video/Site se Secret Code dhoondhein! 🔍");
    window.open(link, '_blank');
}

function verifyTask(taskId, inputId, reward) {
    const userCode = document.getElementById(inputId).value.trim();
    
    // --- 48 HOUR LOCK PER TASK ---
    const lastClaimKey = `last_claim_${taskId}`;
    const lastClaimTime = localStorage.getItem(lastClaimKey);
    const currentTime = new Date().getTime();
    const fortyEightHours = 48 * 60 * 60 * 1000;

    if (lastClaimTime && (currentTime - lastClaimTime < fortyEightHours)) {
        const remainingMs = fortyEightHours - (currentTime - lastClaimTime);
        const remainingHours = Math.ceil(remainingMs / (1000 * 60 * 60));
        tg.showAlert(`✋ Thoda wait! Ye specific task aap kar chuke hain. Agle ${remainingHours} ghante baad phir se try karein.`);
        return;
    }

    // --- 6 UNIQUE SECRET CODES ---
    const TASK_CODES = {
        'yt1': 'YT786',     // YouTube Video 1 Code
        'yt2': 'YT555',     // YouTube Video 2 Code
        'yt3': 'YT999',     // YouTube Video 3 Code
        'web1': 'WEB10',    // Website 1 Code
        'web2': 'WEB20',    // Website 2 Code
        'web3': 'WEB30'     // Website 3 Code
    };

    if (userCode === TASK_CODES[taskId]) {
        let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
        let newBalance = currentCoins + reward;
        
        localStorage.setItem('user_coins', newBalance);
        localStorage.setItem(lastClaimKey, currentTime); // Us task ka time save
        
        updateDisplay();

        tg.sendData(JSON.stringify({ type: "claim_bonus", amount: reward }));
        tg.showAlert(`Correct Code! +${reward} Coins added. ✅ Ye task 2 din ke liye lock ho gaya.`);
        document.getElementById(inputId).value = ""; 
    } else {
        tg.showAlert("Wrong Secret Code! Dhyaan se dekhein. ❌");
    }
}

// 5. Daily Claim
function claimDaily() {
    let lastClaim = localStorage.getItem('last_claim_daily');
    let today = new Date().toDateString();

    if (lastClaim === today) {
        tg.showAlert("Bhai, aaj ka bonus mil gaya! Kal aana. ✨");
        return;
    }

    let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
    let newBalance = currentCoins + 10;
    localStorage.setItem('user_coins', newBalance);
    localStorage.setItem('last_claim_daily', today);
    updateDisplay();
    tg.sendData(JSON.stringify({ type: "claim_bonus", amount: 10 }));
    tg.showAlert("10 Coins added! 🎁");
}

// 6. Withdraw Logic
function requestWithdraw() {
    let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
    const upiId = document.getElementById('upi-id').value.trim();

    if (currentCoins < 1000) {
        tg.showAlert("Min 1000 Coins! ❌");
        return;
    }
    if (!upiId.includes('@')) {
        tg.showAlert("Invalid UPI! 🏦");
        return;
    }

    let history = JSON.parse(localStorage.getItem('withdraw_history')) || [];
    history.unshift({ date: new Date().toLocaleDateString(), amount: currentCoins, status: "Pending ⏳" });
    localStorage.setItem('withdraw_history', JSON.stringify(history));

    tg.sendData(JSON.stringify({ type: "withdraw_request", amount: currentCoins, upi: upiId }));
    localStorage.setItem('user_coins', 0);
    document.getElementById('upi-id').value = "";
    updateDisplay();
    tg.showAlert("Withdrawal Request Sent! ✅");
}

function renderHistory() {
    let history = JSON.parse(localStorage.getItem('withdraw_history')) || [];
    const list = document.getElementById('history-list');
    if (!list) return;
    list.innerHTML = history.length === 0 ? `<p style="text-align:center; color:#94a3b8;">No history found.</p>` : 
    history.map(item => `
        <div style="display:flex; justify-content:space-between; padding:10px; border-bottom:1px solid #1e293b; background:#0f172a; margin-bottom:5px; border-radius:5px;">
            <span>📅 ${item.date}</span>
            <span>💰 ${item.amount}</span>
            <span style="color:#f1c40f;">${item.status}</span>
        </div>`).join('');
}

// 7. Support & Refer
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
    if(navigator.clipboard) {
        navigator.clipboard.writeText(link).then(() => tg.showAlert("Link Copied! 🚀"));
    } else {
        tg.showAlert("Link: " + link);
    }
}
