// 1. Telegram WebApp Setup
const tg = window.Telegram.WebApp;
tg.expand();

// 2. URL se Coins pakadne ka logic (Sync with Database)
const queryParams = new URLSearchParams(window.location.search);
const serverCoins = queryParams.get('coins');

if (serverCoins !== null) {
    localStorage.setItem('user_coins', serverCoins);
}

// Balance update karne ka function
function updateDisplay() {
    let currentBalance = localStorage.getItem('user_coins') || 0;
    const balanceEl = document.getElementById('balance');
    if (balanceEl) {
        balanceEl.innerText = currentBalance + " 🪙";
    }
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
        el.classList.add('active');
        document.getElementById('tab-title').innerText = tabId.charAt(0).toUpperCase() + tabId.slice(1);
        
        // Refer link logic
        if(tabId === 'refer') {
            const userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "guest";
            const inviteLink = `https://t.me/Codetearn_bot?start=${userId}`;
            const displayLink = document.getElementById('display-link');
            if(displayLink) displayLink.innerText = inviteLink;
        }
    }
}

// 4. Daily Claim Function
function claimDaily() {
    let lastClaim = localStorage.getItem('last_claim');
    let today = new Date().toDateString();

    if (lastClaim === today) {
        tg.showAlert("Already claimed today! ✨");
        return;
    }

    let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
    let newBalance = currentCoins + 10;
    localStorage.setItem('user_coins', newBalance);
    localStorage.setItem('last_claim', today);
    
    updateDisplay();

    tg.sendData(JSON.stringify({ type: "claim_bonus", amount: 10 }));
    tg.showAlert("10 Coins added! 🎁");
}

// 5. NEW: Extra Tasks Logic (Timer based)
function completeTask(type, link, reward) {
    tg.showAlert(`Opening ${type}. Wait 60s for ${reward} coins! Don't close the App. ⏳`);
    window.open(link, '_blank');

    setTimeout(() => {
        let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
        let newBalance = currentCoins + reward;
        
        localStorage.setItem('user_coins', newBalance);
        updateDisplay();

        tg.sendData(JSON.stringify({ type: "claim_bonus", amount: reward }));
        tg.showAlert(`Task Verified! ${reward} coins added. ✅`);
    }, 60000); // 60 Seconds timer
}

// 6. NEW: Withdraw Logic
function requestWithdraw() {
    let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
    const upiId = document.getElementById('upi-id').value;

    if (currentCoins < 1000) {
        tg.showAlert("Minimum 1000 coins required to withdraw! ❌");
        return;
    }

    if (!upiId.includes('@')) {
        tg.showAlert("Please enter a valid UPI ID! 🏦");
        return;
    }

    tg.sendData(JSON.stringify({
        type: "withdraw_request",
        amount: currentCoins,
        upi: upiId
    }));

    // Reset coins local me (Database bot.py handle karega)
    localStorage.setItem('user_coins', 0);
    updateDisplay();
    tg.showAlert("Withdrawal Request Sent! Admin will pay within 24h. ✅");
}

// 7. Support Logic
function sendSupport() {
    const msgInput = document.getElementById('support-msg');
    const msg = msgInput.value;
    if(!msg.trim()) return tg.showAlert("Please write something.");
    
    tg.sendData(JSON.stringify({type: 'support', message: msg}));
    tg.showAlert("Sent to Admin! ✅");
    msgInput.value = "";
}

// 8. Referral Logic
function inviteFriend() {
    const userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "guest";
    const link = `https://t.me/Codetearn_bot?start=${userId}`;
    
    if(navigator.clipboard) {
        navigator.clipboard.writeText(link).then(() => {
            tg.showAlert("Referral Link Copied! 🚀");
        }).catch(() => {
            tg.showAlert("Link: " + link);
        });
    } else {
        tg.showAlert("Link: " + link);
    }
}
