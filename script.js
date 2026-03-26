const tg = window.Telegram.WebApp;
tg.expand();

// 1. Apne Render ka link yahan dalo
const API_BASE_URL = "https://earning-bot-b27x.onrender.com"; 

const urlParams = new URLSearchParams(window.location.search);
const userId = urlParams.get('user_id') || tg.initDataUnsafe?.user?.id;

// --- API se Data Load Karne Wala Function ---
async function fetchLiveData() {
    if (!userId) return;

    try {
        const response = await fetch(`${API_BASE_URL}/get_user/${userId}`);
        const data = await response.json();

        if (data.status === "success") {
            // Balance Update
            document.getElementById('balance').innerText = `${data.coins} 🪙`;
            
            // Leaderboard Update
            updateLeaderboardUI(data.leaderboard);
            
            // Referral Link Update
            const botUsername = "TUMHARE_BOT_USERNAME"; // Apne bot ka username yahan dalo
            document.getElementById('display-link').innerText = `https://t.me/${botUsername}?start=${userId}`;
        }
    } catch (error) {
        console.error("API Error:", error);
    }
}

// Leaderboard ko UI mein dikhane ke liye
function updateLeaderboardUI(leaderboardData) {
    const list = document.getElementById('leaderboard-list');
    if (leaderboardData === "none") {
        list.innerHTML = "<p style='text-align:center;'>No users yet.</p>";
        return;
    }

    const players = leaderboardData.split('|');
    let html = "";
    players.forEach((p, index) => {
        const [id, coins] = p.split(':');
        html += `
            <div style="display:flex; justify-content:space-between; padding:10px; border-bottom:1px solid #334155;">
                <span>${index + 1}. ID: ${id}</span>
                <span style="color:#f1c40f;">${coins} 🪙</span>
            </div>`;
    });
    list.innerHTML = html;
}

// --- Tab Switching ---
function switchTab(tabId, el) {
    document.querySelectorAll('.tab-content').forEach(t => t.style.display = 'none');
    document.getElementById(tabId).style.display = 'block';
    
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    if(el.classList.contains('nav-item')) el.classList.add('active');
    
    document.getElementById('tab-title').innerText = tabId.charAt(0).toUpperCase() + tabId.slice(1);
}

// --- Actions (Bot ko data bhejna) ---
function claimDaily() {
    tg.sendData(JSON.stringify({type: 'claim_bonus', amount: 10}));
    tg.close(); // Data bhej kar bot band hoga taaki refresh ho sake
}

function requestWithdraw() {
    const upi = document.getElementById('upi-id').value;
    const balance = parseInt(document.getElementById('balance').innerText);
    
    if (balance < 1000) return alert("Min. 1000 coins required!");
    if (!upi.includes('@')) return alert("Enter valid UPI ID!");

    tg.sendData(JSON.stringify({
        type: 'withdraw_request',
        amount: balance,
        upi: upi
    }));
    tg.close();
}

// --- Auto Refresh Logic ---
// 1. Pehli baar load hote hi data lao
fetchLiveData();

// 2. Har 5 second mein background mein check karo (Fast Sync)
setInterval(fetchLiveData, 5000); 
