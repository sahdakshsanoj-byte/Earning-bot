const tg = window.Telegram.WebApp;
tg.expand();

// 1. Initial Data Fetching
const queryParams = new URLSearchParams(window.location.search);
const serverCoins = queryParams.get('coins') || 0;
const serverAds = queryParams.get('ads') || 0;
const refListStr = queryParams.get('ref_list') || "none";
const topUsersStr = queryParams.get('top_users') || "none";

if (serverCoins !== null) {
    localStorage.setItem('user_coins', serverCoins);
    localStorage.setItem('daily_ads_count', serverAds);
}

// 2. Main Display Function
function updateDisplay() {
    let currentBalance = localStorage.getItem('user_coins') || 0;
    let adsDone = localStorage.getItem('daily_ads_count') || 0;
    
    const balanceEl = document.getElementById('balance');
    if (balanceEl) balanceEl.innerText = currentBalance + " 🪙";

    const adText = document.getElementById('ad-count-text');
    const adProgress = document.getElementById('ad-progress');
    if (adText && adProgress) {
        adText.innerText = `${adsDone}/5`;
        adProgress.style.width = (adsDone * 20) + "%";
    }

    renderHistory();
    renderReferrals();
    renderLeaderboard();
    renderAllTasks(); // Naya function jo sab load karega
}

// 3. Dynamic Tasks & Sponsors Rendering
function renderAllTasks() {
    if (typeof APP_CONFIG === 'undefined') return;

    // Load YouTube Tasks (3 tasks from config)
    const ytList = document.getElementById('yt-tasks-list');
    if (ytList) {
        ytList.innerHTML = APP_CONFIG.youtube_tasks.map(task => `
            <div class="task-item" style="margin: 15px 0; padding-bottom: 10px; border-bottom: 1px solid #334155;">
                <p style="font-size: 13px;">Watch & Earn (+${task.reward})</p>
                <button class="btn-sm" style="background:#ff0000; width:100%; margin:5px 0;" onclick="openTask('${task.link}')">Watch Video</button>
                <div style="display: flex; gap: 5px;">
                    <input type="text" id="input-${task.id}" placeholder="Code" style="flex:1; padding:8px; background:#0f172a; color:white; border:1px solid #334155; border-radius:5px;">
                    <button class="btn-sm" onclick="verifyTask('${task.id}', 'input-${task.id}', ${task.reward})">Verify</button>
                </div>
            </div>
        `).join('');
    }

    // Load Website Tasks (3 tasks from config)
    const webList = document.getElementById('web-tasks-list');
    if (webList) {
        webList.innerHTML = APP_CONFIG.website_tasks.map(task => `
            <div class="task-item" style="margin: 15px 0; padding-bottom: 10px; border-bottom: 1px solid #334155;">
                <p style="font-size: 13px;">Visit & Earn (+${task.reward})</p>
                <button class="btn-sm" style="background:#3498db; width:100%; margin:5px 0;" onclick="openTask('${task.link}')">Visit Site</button>
                <div style="display: flex; gap: 5px;">
                    <input type="text" id="input-${task.id}" placeholder="Code" style="flex:1; padding:8px; background:#0f172a; color:white; border:1px solid #334155; border-radius:5px;">
                    <button class="btn-sm" onclick="verifyTask('${task.id}', 'input-${task.id}', ${task.reward})">Verify</button>
                </div>
            </div>
        `).join('');
    }

    // Load Sponsors (3 channels from config)
    const sponsorList = document.getElementById('sponsor-list');
    if (sponsorList) {
        sponsorList.innerHTML = APP_CONFIG.sponsors.map(sp => `
            <div class="sponsor-card">
                <div><p style="font-size:14px; margin:0;">${sp.name}</p><span style="color:#0088cc; font-size:11px;">+${sp.reward} Coins</span></div>
                <button class="btn-sm" style="background:#0088cc;" onclick="checkSponsor('${sp.username}')">Join & Verify</button>
            </div>
        `).join('');
    }
}

// 4. Tab Switching
function switchTab(tabId, el) {
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.style.display = 'none';
        tab.classList.remove('active-tab');
    });
    document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));

    const target = document.getElementById(tabId);
    if (target) {
        target.style.display = 'block';
        target.classList.add('active-tab');
        if (el) el.classList.add('active');
        document.getElementById('tab-title').innerText = tabId.charAt(0).toUpperCase() + tabId.slice(1);
        
        if(tabId === 'refer') {
            const userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "guest";
            document.getElementById('display-link').innerText = `https://t.me/Cdotern_bot?start=${userId}`;
        }
    }
}

// 5. Utility Functions
function openTask(link) { window.open(link, '_blank'); }

function verifyTask(taskId, inputId, reward) {
    const userCode = document.getElementById(inputId).value.trim();
    // Codes ko config se uthana
    const allTasks = [...APP_CONFIG.youtube_tasks, ...APP_CONFIG.website_tasks];
    const task = allTasks.find(t => t.id === taskId);

    if (task && userCode === task.code) {
        let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
        localStorage.setItem('user_coins', currentCoins + reward);
        updateDisplay();
        tg.sendData(JSON.stringify({ type: "claim_bonus", amount: reward }));
        tg.showAlert("✅ Correct! Coins added.");
        document.getElementById(inputId).value = "";
    } else {
        tg.showAlert("❌ Wrong Secret Code!");
    }
}

function watchAd() {
    let adsDone = parseInt(localStorage.getItem('daily_ads_count')) || 0;
    if (adsDone >= 5) return tg.showAlert("❌ Daily limit 5/5 reached!");
    window.open(APP_CONFIG.daily_ads.ad_link, "_blank"); 
    tg.sendData(JSON.stringify({ type: "ad_click" }));
    localStorage.setItem('daily_ads_count', adsDone + 1);
    localStorage.setItem('user_coins', (parseInt(localStorage.getItem('user_coins')) || 0) + 10);
    updateDisplay();
}

function checkSponsor(user) { tg.sendData(JSON.stringify({ type: "check_sponsor", channel_id: user })); }

function renderLeaderboard() {
    const list = document.getElementById('leaderboard-list');
    if (!list || !topUsersStr || topUsersStr === "none") return;
    list.innerHTML = topUsersStr.split('|').map((u, i) => {
        const [id, score] = u.split(':');
        return `<div style="display:flex; justify-content:space-between; padding:12px; border-bottom:1px solid #1e293b;"><span>${i+1}. ${id}</span><span style="color:#f1c40f;">${score} 🪙</span></div>`;
    }).join('');
}

function renderReferrals() {
    const list = document.getElementById('refer-list-container');
    if (!list) return;
    if (!refListStr || refListStr === "none") { list.innerHTML = "No referrals yet."; return; }
    list.innerHTML = refListStr.split(',').map(id => `<div style="padding:10px; border-bottom:1px solid #1e293b;">👤 ID: ${id} (+50 ✅)</div>`).join('');
}

function claimDaily() {
    let last = localStorage.getItem('last_daily');
    let today = new Date().toDateString();
    if (last === today) return tg.showAlert("Aaj ka bonus mil gaya!");
    localStorage.setItem('user_coins', (parseInt(localStorage.getItem('user_coins')) || 0) + 10);
    localStorage.setItem('last_daily', today);
    updateDisplay();
    tg.sendData(JSON.stringify({ type: "claim_bonus", amount: 10 }));
    tg.showAlert("🎁 10 Coins added!");
}

// script.js ke sabse niche ye dalo ya pura replace karo
function updateDisplay() {
    try {
        // Balance & Ads
        document.getElementById('balance').innerText = (localStorage.getItem('user_coins') || 0) + " 🪙";
        const adsDone = localStorage.getItem('daily_ads_count') || 0;
        document.getElementById('ad-count-text').innerText = `${adsDone}/5`;
        document.getElementById('ad-progress').style.width = (adsDone * 20) + "%";

        // Yahan se Tasks Load honge
        renderAllTasks(); 
    } catch (e) { console.log("Display Error:", e); }
}

function renderAllTasks() {
    // Check agar config load hui hai
    if (typeof APP_CONFIG === 'undefined') {
        console.error("Config file not found!");
        return;
    }

    const ytList = document.getElementById('yt-tasks-list');
    const webList = document.getElementById('web-tasks-list');

    // YouTube Tasks
    if (ytList) {
        ytList.innerHTML = APP_CONFIG.youtube_tasks.map(task => `
            <div class="task-item" style="margin: 15px 0; padding-bottom: 10px; border-bottom: 1px solid #334155;">
                <p style="font-size: 13px;">Watch & Earn (+${task.reward})</p>
                <button class="btn-sm" style="background:#ff0000; width:100%; margin:5px 0;" onclick="openTask('${task.link}')">Watch Video</button>
                <div style="display: flex; gap: 5px;">
                    <input type="text" id="input-${task.id}" placeholder="Code" style="flex:1; padding:8px; background:#0f172a; color:white; border:1px solid #334155; border-radius:5px;">
                    <button class="btn-sm" onclick="verifyTask('${task.id}', 'input-${task.id}', ${task.reward})">Verify</button>
                </div>
            </div>
        `).join('');
    }

    // Website Tasks
    if (webList) {
        webList.innerHTML = APP_CONFIG.website_tasks.map(task => `
            <div class="task-item" style="margin: 15px 0; padding-bottom: 10px; border-bottom: 1px solid #334155;">
                <p style="font-size: 13px;">Visit & Earn (+${task.reward})</p>
                <button class="btn-sm" style="background:#3498db; width:100%; margin:5px 0;" onclick="openTask('${task.link}')">Visit Site</button>
                <div style="display: flex; gap: 5px;">
                    <input type="text" id="input-${task.id}" placeholder="Code" style="flex:1; padding:8px; background:#0f172a; color:white; border:1px solid #334155; border-radius:5px;">
                    <button class="btn-sm" onclick="verifyTask('${task.id}', 'input-${task.id}', ${task.reward})">Verify</button>
                </div>
            </div>
        `).join('');
    }
}

// Ye line sabse zaroori hai!
window.onload = updateDisplay; 

