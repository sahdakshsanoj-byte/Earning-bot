// Testing Script
const tg = window.Telegram.WebApp;
tg.expand();

alert("Script Load Ho Gayi! ✅"); // Agar ye alert nahi aata, toh file connect nahi hai.

function switchTab(tabId, el) {
    alert("Button Click Hua: " + tabId); // Agar ye nahi aata, toh onclick mein galti hai.

    // Sabko hide karo
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.style.display = 'none';
    });

    // Target ko dikhao
    const target = document.getElementById(tabId);
    if (target) {
        target.style.setProperty('display', 'block', 'important');
        alert("Tab '" + tabId + "' ab dikhna chahiye!");
    } else {
        alert("Galti: HTML mein id='" + tabId + "' nahi mili!");
    }
}
