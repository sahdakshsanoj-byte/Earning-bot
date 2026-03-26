const tg = window.Telegram.WebApp;
tg.expand();

// 1. Data Fetching from URL
const queryParams = new URLSearchParams(window.location.search);
const serverCoins = queryParams.get('coins') || 0;
const serverAds = queryParams.get('ads') || 0;
const topUsersStr = queryParams.get('top') || "none";
const refListStr = queryParams.get('ref_list') || "none";

// Memory Sync
localStorage.setItem('user_coins', serverCoins);
localStorage.setItem('daily_ads_count', serverAds);

// 2. Main Display & Rendering Logic
function updateDisplay() {
    try {
        // Balance Update
        const balanceEl = document.getElementById('balance');
        if (balanceEl) balanceEl.innerText = (localStorage.getItem('user_coins') || 0) + " 🪙";

        // Ads Progress Update
        const adText = document.getElementById('ad-count-text');
        const adProgress = document.getElementById('ad-progress');
        if (adText && adProgress) {
            let adsDone = localStorage.getItem('daily_ads_count') || 0;
            adText.innerText = `${adsDone}/5`;
            adProgress.style.width = (adsDone * 20) + "%";
        }

        // Call All Renderers
        renderAllTasks(); 
        renderLeaderboard();
        renderReferrals();
        renderHistory();
    } catch (e) { console.error("Display Error:", e); }
}

// 3. Dynamic Tasks & Sponsors (From config.js)
function renderAllTasks() {
    if (typeof APP_CONFIG === 'undefined') return;

    // YT Tasks
    const ytList = document.getElementById('yt-tasks-list');
    if (ytList) {
        ytList.innerHTML = APP_CONFIG.youtube_tasks.map(task => `
            <div class="task-item">
                <p style="font-size: 13px;">Watch & Earn (+${task.reward})</p>
                <button class="btn-sm" style="background:#ff0000; width:100%; margin:5px 0;" onclick="openTask('${task.link}')">Watch Video</button>
                <div style="display: flex; gap: 5px;">
                    <input type="text" id="input-${task.id}" placeholder="Code" style="flex:1; padding:8px; background:#0f172a; color:white; border:1px solid #334155;">
                    <button class="btn-sm" onclick="verifyTask('${task.id}', 'input-${task.id}', ${task.reward})">Verify</button>
                </div>
            </div>
        `).join('');
    }

    // Web Tasks
    const webList = document.getElementById('web-tasks-list');
    if (webList) {
        webList.innerHTML = APP_CONFIG.website_tasks.map(task => `
            <div class="task-item">
                <p style="font-size: 13px;">Visit & Earn (+${task.reward})</p>
                <button class="btn-sm" style="background:#3498db; width:100%; margin:5px 0;" onclick="openTask('${task.link}')">Visit Site</button>
                <div style="display: flex; gap: 5px;">
                    <input type="text" id="input-${task.id}" placeholder="Code" style="flex:1; padding:8px; background:#0f172a; color:white; border:1px solid #334155;">
                    <button class="btn-sm" onclick="verifyTask('${task.id}', 'input-${task.id}', ${task.reward})">Verify</button>
                </div>
            </div>
        `).join('');
    }

    // Sponsors
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

// 4. Verification & Actions
function verifyTask(taskId, inputId, reward) {
    const userCode = document.getElementById(inputId).value.trim();
    const allTasks = [...APP_CONFIG.youtube_tasks, ...APP_CONFIG.website_tasks];
    const task = allTasks.find(t => t.id === taskId);

    if (task && userCode === task.code) {
        let currentCoins = parseInt(localStorage.getItem('user_coins')) || 0;
        localStorage.setItem('user_coins', currentCoins + reward);
        updateDisplay();
        tg.sendData(JSON.stringify({ type: "claim_bonus", amount: reward }));
        tg.showAlert("✅ Correct! Coins added.");
    } else { tg.showAlert("❌ Wrong Secret Code!"); }
}

function switchTab(tabId, el) {
    document.querySelectorAll('.tab-content').forEach(tab => tab.style.display = 'none');
    document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));
    document.getElementById(tabId).style.display = 'block';
    if (el) el.classList.add('active');
    
    if(tabId === 'refer') {
        const userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "guest";
        document.getElementById('display-link').innerText = `https://t.me/Cdotern_bot?start=${userId}`;
    }
}

function watchAd() {
    let adsDone = parseInt(localStorage.getItem('daily_ads_count')) || 0;
    if (adsDone >= 5) return tg.showAlert("❌ Daily limit reached!");
    window.open(APP_CONFIG.daily_ads.ad_link, "_blank"); 
    tg.sendData(JSON.stringify({ type: "ad_click" }));
    localStorage.setItem('daily_ads_count', adsDone + 1);
    localStorage.setItem('user_coins', (parseInt(localStorage.getItem('user_coins')) || 0) + 10);
    updateDisplay();
}

function renderLeaderboard() {
    const list = document.getElementById('leaderboard-list');
    if (!list || topUsersStr === "none") return;
    list.innerHTML = topUsersStr.split('|').map((u, i) => {
        const [id, score] = u.split(':');
        return `<div style="display:flex; justify-content:space-between; padding:10px;"><span>${i+1}. ${id}</span><span>${score} 🪙</span></div>`;
    }).join('');
}

function renderReferrals() {
    const list = document.getElementById('refer-list-container');
    if (!list) return;
    if (refListStr === "none") { list.innerHTML = "No referrals yet."; return; }
    list.innerHTML = refListStr.split(',').map(id => `<div style="padding:10px;">👤 ID: ${id} (+50 ✅)</div>`).join('');
}

function openTask(link) { window.open(link, '_blank'); }
function checkSponsor(user) { tg.sendData(JSON.stringify({ type: "check_sponsor", channel_id: user })); }
function claimDaily() { /* Same daily bonus logic */ }
function renderHistory() { /* Same history logic */ }

// Start
window.onload = updateDisplay;
    
