/* Nucleus360 Trading Dashboard — JS Helpers */

// ============================================================
// HTMX Configuration
// ============================================================

document.body.addEventListener('htmx:configRequest', function(evt) {
    // Include session cookie in all HTMX requests
    evt.detail.headers['X-Requested-With'] = 'XMLHttpRequest';
});

document.body.addEventListener('htmx:responseError', function(evt) {
    if (evt.detail.xhr.status === 401 || evt.detail.xhr.status === 403) {
        window.location.href = '/login';
    }
});

// ============================================================
// Expandable Table Rows
// ============================================================

function toggleExpand(id) {
    const row = document.getElementById('expand-' + id);
    if (row) {
        row.classList.toggle('open');
    }
}

// ============================================================
// Tab Switching
// ============================================================

function switchTab(tabGroup, tabName) {
    // Update tab buttons
    document.querySelectorAll('[data-tab-group="' + tabGroup + '"]').forEach(function(btn) {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    // Update tab content
    document.querySelectorAll('[data-tab-content="' + tabGroup + '"]').forEach(function(content) {
        content.style.display = content.dataset.tabPane === tabName ? 'block' : 'none';
    });
}

// ============================================================
// Chart.js — Equity Curve
// ============================================================

let equityChart = null;

function initEquityChart(canvasId) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    equityChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Portfolio Equity',
                data: [],
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 0,
                pointHoverRadius: 4,
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index',
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(30, 41, 59, 0.95)',
                    titleColor: '#f1f5f9',
                    bodyColor: '#94a3b8',
                    borderColor: 'rgba(148, 163, 184, 0.2)',
                    borderWidth: 1,
                    padding: 12,
                    titleFont: { family: 'DM Sans', weight: '600' },
                    bodyFont: { family: 'JetBrains Mono', size: 13 },
                    callbacks: {
                        label: function(context) {
                            return '$' + context.parsed.y.toLocaleString(undefined, {
                                minimumFractionDigits: 2,
                                maximumFractionDigits: 2
                            });
                        }
                    }
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: '#64748b',
                        font: { family: 'DM Sans', size: 11 },
                        maxTicksLimit: 10,
                    },
                    grid: {
                        color: 'rgba(148, 163, 184, 0.06)',
                    },
                    border: { color: 'rgba(148, 163, 184, 0.1)' }
                },
                y: {
                    ticks: {
                        color: '#64748b',
                        font: { family: 'JetBrains Mono', size: 11 },
                        callback: function(value) {
                            return '$' + value.toLocaleString();
                        }
                    },
                    grid: {
                        color: 'rgba(148, 163, 184, 0.06)',
                    },
                    border: { color: 'rgba(148, 163, 184, 0.1)' }
                }
            }
        }
    });
}

function loadEquityData(period) {
    // Update active button
    document.querySelectorAll('.period-btn').forEach(function(btn) {
        btn.classList.toggle('active', btn.dataset.period === period);
    });

    fetch('/api/equity?period=' + period)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!equityChart) return;
            equityChart.data.labels = data.labels;
            equityChart.data.datasets[0].data = data.values;
            equityChart.update('none');
        })
        .catch(function(err) {
            console.error('Failed to load equity data:', err);
        });
}

// ============================================================
// Number Formatting
// ============================================================

function formatCurrency(value) {
    return '$' + parseFloat(value).toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

function formatPercent(value) {
    var sign = value >= 0 ? '+' : '';
    return sign + parseFloat(value).toFixed(2) + '%';
}
