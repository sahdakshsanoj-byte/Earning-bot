// 1. Telegram WebApp Setup
const tg = window.Telegram.WebApp;
tg.expand();

// 2. Tab Switching Function
function switchTab(tabId, el) {
    console.log("Switching to: " + tabId);

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
    } else {
        alert("Bhai, id='" + tabId + "' wala div nahi mila!");
    }
}

// 3. Rewards Logic
let coins = parseInt(localStorage.getItem('user_coins')) || 0;
const balanceEl = document.getElementById('balance');
if(balanceEl) balanceEl.innerText = coins + " 🪙";

function claimDaily() {
    coins += 10;
    localStorage.setItem('user_coins', coins);
    if(balanceEl) balanceEl.innerText = coins + " 🪙";
    tg.showAlert("10 Coins Mil Gaye! 🎁");
}

// 4. Support Logic
function sendSupport() {
    const msgInput = document.getElementById('support-msg');
    const msg = msgInput.value;
    
    if(!msg.trim()) {
        tg.showAlert("Please write here if you face any problem");
        return;
    }
    
    // JSON data bhej rahe hain Python bot ko
    tg.sendData(JSON.stringify({type: 'support', message: msg}));
    tg.showAlert("Admin received your message and will reply in 5-6 hours! ✅");
    msgInput.value = "";
}

// 5. Referral Logic
function inviteFriend() {
    const userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "guest";
    
    // Yahan '@' hata diya hai kyunki URL mein seedha naam aata hai
    const botUsername = "Cdotern_bot"; 
    const link = `https://t.me/Codetern_bot?start=${userId}`;
    
    if(navigator.clipboard) {
        navigator.clipboard.writeText(link).then(() => {
            tg.showAlert("Referral Link Copied successfully! 🚀");
        }).catch(() => {
            tg.showAlert("Link: " + link);
        });
    } else {
        tg.showAlert("Link: " + link);
    }
}
