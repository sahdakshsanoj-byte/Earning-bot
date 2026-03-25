// 1. Telegram WebApp Setup
const tg = window.Telegram.WebApp;
tg.expand();

// 2. Tab Switching Function (The Fix)
function switchTab(tabId, el) {
    // Check karne ke liye ki button kaam kar raha hai
    console.log("Switching to: " + tabId);

    // Saare tabs ko hide karo
    const tabs = document.querySelectorAll('.tab-content');
    tabs.forEach(tab => {
        tab.style.display = 'none';
        tab.classList.remove('active-tab');
    });

    // Saare buttons se active class hatao
    const buttons = document.querySelectorAll('.nav-item');
    buttons.forEach(btn => btn.classList.remove('active'));

    // Target tab ko dikhao
    const target = document.getElementById(tabId);
    if (target) {
        target.style.display = 'block';
        target.classList.add('active-tab');
        el.classList.add('active');
        
        // Header title badlo
        document.getElementById('tab-title').innerText = tabId.charAt(0).toUpperCase() + tabId.slice(1);
    } else {
        alert("Bhai, id='" + tabId + "' wala div nahi mila!");
    }
}

// 3. Rewards & Support Logic
let coins = parseInt(localStorage.getItem('user_coins')) || 0;
const balanceEl = document.getElementById('balance');
if(balanceEl) balanceEl.innerText = coins + " 🪙";

function claimDaily() {
    coins += 10;
    localStorage.setItem('user_coins', coins);
    if(balanceEl) balanceEl.innerText = coins + " 🪙";
    tg.showAlert("10 Coins Mil Gaye! 🎁");
}

function sendSupport() {
    const msg = document.getElementById('support-msg').value;
    if(!msg.trim()) {
        tg.showAlert("please write here you face any problem");
        return;
    }
    tg.sendData(JSON.stringify({type: 'support', message: msg}));
    tg.showAlert("Admin reached your message and reply 5-6 hours! ✅");
    document.getElementById('support-msg').value = "";
}

function inviteFriend() {
    const userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "guest";
    const botUsername = "@Cdotern_bot"; // Apna bot username yahan daalna
    const link = `https://t.me/${@Cdotern_bot}?start=${userId}`;
    
    if(navigator.clipboard) {
        navigator.clipboard.writeText(link);
        tg.showAlert("Referral Link Copy successfully! 🚀");
    } else {
        tg.showAlert("Link: " + link);
    }
}
