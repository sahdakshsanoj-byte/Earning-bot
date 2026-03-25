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
    renderHistory(); // Har baar balance update pe history bhi refresh ho
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
        if (el.classList) el.classList.add('active'); // Nav buttons ke liye
        document.getElementById('tab-title').innerText = tabId.charAt(0).toUpperCase() + tabId.slice(1);
        
        if(tabId === 'refer') {
            const userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "guest";
            const inviteLink = `https://t.me/Codetearn_bot?start=${userId}`;
            document.getElementById('display-link').innerText = inviteLink;
        }
    }
}

// 4. Secret Code Task Logic (No Timer, Just Code Verification)
function openTask(link) {
    tg.showAlert("Video/Website khul rahi hai. Secret Code dhoondhein aur wapas aakar enter karein! 🔍");
    window.open(link, '_blank');
}

function verifyTask(type, inputId, reward) {
    const userCode = document.getElementById(inputId).value.trim();
    
    // YAHAN APNA SECRET CODE SET KARO (Video me jo dikhaoge)
    const SECRET_YT = "YT786"; 
    const SECRET_WEB = "WEB99";

    let correctCode = (type === 'youtube') ? SECRET_YT : SECRET_WEB;

    if (userCode === correctCode) {
        let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
        let newBalance = currentCoins + reward;
        
        localStorage.setItem('user_coins', newBalance);
        updateDisplay();

        tg.sendData(JSON.stringify({ type: "claim_bonus", amount: reward }));
        tg.showAlert(`Correct Code! +${reward} Coins added. ✅`);
        document.getElementById(inputId).value = ""; 
    } else {
        tg.showAlert("Wrong Secret Code! Video dhyaan se dekhein. ❌");
    }
}

// 5. Withdraw Logic with History Logging
function requestWithdraw() {
    let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
    const upiId = document.getElementById('upi-id').value.trim();

    if (currentCoins < 1000) {
        tg.showAlert("Minimum 1000 coins required! ❌");
        return;
    }
    if (!upiId.includes('@')) {
        tg.showAlert("Invalid UPI ID! 🏦");
        return;
    }

    // History update
    let history = JSON.parse(localStorage.getItem('withdraw_history')) || [];
    history.unshift({ 
        date: new Date().toLocaleDateString(), 
        amount: currentCoins, 
        status: "Pending ⏳" 
    });
    localStorage.setItem('withdraw_history', JSON.stringify(history));

    tg.sendData(JSON.stringify({
        type: "withdraw_request",
        amount: currentCoins,
        upi: upiId
    }));

    localStorage.setItem('user_coins', 0);
    document.getElementById('upi-id').value = "";
    updateDisplay();
    tg.showAlert("Withdrawal Request Sent! Check History (3-dots). ✅");
}

function renderHistory() {
    let history = JSON.parse(localStorage.getItem('withdraw_history')) || [];
    const list = document.getElementById('history-list');
    if (!list) return;

    if (history.length === 0) {
        list.innerHTML = `<p style="text-align:center; color:#94a3b8;">No history yet.</p>`;
    } else {
        list.innerHTML = history.map(item => `
            <div style="display:flex; justify-content:space-between; padding:10px; border-bottom:1px solid #1e293b; background:#0f172a; margin-bottom:5px; border-radius:5px;">
                <span>📅 ${item.date}</span>
                <span>💰 ${item.amount}</span>
                <span style="color:#f1c40f;">${item.status}</span>
            </div>
        `).join('');
    }
}

// 6. Support & Refer
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
