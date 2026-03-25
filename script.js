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
        
        // Agar Refer tab khule toh link dikhao
        if(tabId === 'refer') {
            const userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "guest";
            const inviteLink = `https://t.me/Codetearn_bot?start=${userId}`;
            const displayLink = document.getElementById('display-link');
            if(displayLink) displayLink.innerText = inviteLink;
        }
    }
}

// 4. Daily Claim Function (With Database Sync)
function claimDaily() {
    let lastClaim = localStorage.getItem('last_claim');
    let today = new Date().toDateString();

    if (lastClaim === today) {
        tg.showAlert("You have already claimed your bonus today! ✨");
        return;
    }

    // Local update
    let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
    let newBalance = currentCoins + 10;
    localStorage.setItem('user_coins', newBalance);
    localStorage.setItem('last_claim', today);
    
    updateDisplay();

    // 🚀 Bot ko signal bhejo database update ke liye
    tg.sendData(JSON.stringify({
        type: "claim_bonus",
        amount: 10
    }));

    tg.showAlert("Congratulations! 10 Coins added to your account. 🎁");
}

// 5. Support Logic
function sendSupport() {
    const msgInput = document.getElementById('support-msg');
    const msg = msgInput.value;
    
    if(!msg.trim()) {
        tg.showAlert("Please write your message here.");
        return;
    }
    
    tg.sendData(JSON.stringify({type: 'support', message: msg}));
    tg.showAlert("Message sent! Admin will reply in 5-6 hours. ✅");
    msgInput.value = "";
}

// 6. Referral Logic
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
