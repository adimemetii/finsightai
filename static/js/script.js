// =====================================================
// UTILITY FUNCTIONS
// =====================================================

// Toast notification
function showToast(message, type = 'info') {
    const alertClass = {
        'success': 'alert-success',
        'danger': 'alert-danger',
        'warning': 'alert-warning',
        'info': 'alert-info'
    };

    const alert = document.createElement('div');
    alert.className = `alert ${alertClass[type]} alert-dismissible fade show`;
    const text = document.createTextNode(String(message));
    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'btn-close';
    closeButton.dataset.bsDismiss = 'alert';
    closeButton.setAttribute('aria-label', 'Close');
    alert.append(text, closeButton);

    const container = document.querySelector('.page-body .container-xl') || document.body;
    container.insertBefore(alert, container.firstChild);

    setTimeout(() => {
        alert.remove();
    }, 5000);
}

// Format currency
function formatCurrency(value) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD'
    }).format(value);
}

// Format date
function formatDate(dateString) {
    const options = { year: 'numeric', month: 'long', day: 'numeric' };
    return new Date(dateString).toLocaleDateString('en-US', options);
}

// API Error handler
function handleApiError(error) {
    const message = error.response?.data?.error || error.message || 'An error occurred';
    console.error('API Error:', message);
    showToast(message, 'danger');
}

// Every AJAX write carries the same session-bound CSRF token as server-rendered forms.
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
if (window.axios && csrfToken) {
    window.axios.defaults.headers.common['X-CSRFToken'] = csrfToken;
}

// =====================================================
// NAVBAR ANIMATION
// =====================================================

document.addEventListener('DOMContentLoaded', () => {
    // Add scroll animation to navbar
    let lastScrollTop = 0;
    const navbar = document.querySelector('.navbar');

    if (navbar) {
        window.addEventListener('scroll', () => {
            let scrollTop = window.pageYOffset || document.documentElement.scrollTop;

            if (scrollTop > 100) {
                navbar.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
            } else {
                navbar.style.boxShadow = '0 2px 8px rgba(0,0,0,0.1)';
            }

            lastScrollTop = scrollTop;
        });
    }
});

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('form[data-submit-state]').forEach((form) => {
        form.addEventListener('submit', () => {
            if (!form.checkValidity()) return;
            const button = form.querySelector('button[type="submit"]');
            if (button) {
                button.disabled = true;
                button.setAttribute('aria-busy', 'true');
            }
        });
    });
});

// =====================================================
// FORM VALIDATION
// =====================================================

function validateForm(formId) {
    const form = document.getElementById(formId);
    if (!form) return true;

    return form.checkValidity() === false ? false : true;
}

// =====================================================
// SMOOTH PAGE TRANSITIONS
// =====================================================

document.addEventListener('DOMContentLoaded', () => {
    // Add fade-in animation to cards
    const cards = document.querySelectorAll('.card');
    cards.forEach((card, index) => {
        card.style.animation = `slideIn 0.3s ease-out ${index * 0.1}s`;
        card.style.animationFillMode = 'both';
    });
});

// =====================================================
// TABLE SORTING
// =====================================================

function makeSortable(tableId) {
    const table = document.getElementById(tableId);
    if (!table) return;

    const headers = table.querySelectorAll('th');

    headers.forEach((header, index) => {
        header.style.cursor = 'pointer';
        header.addEventListener('click', () => sortTable(table, index));
    });
}

function sortTable(table, columnIndex) {
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    let isAscending = true;

    const headerCell = table.querySelector(`th:nth-child(${columnIndex + 1})`);
    if (headerCell.classList.contains('sort-asc')) {
        isAscending = false;
        headerCell.classList.remove('sort-asc');
        headerCell.classList.add('sort-desc');
    } else {
        headerCell.classList.remove('sort-desc');
        headerCell.classList.add('sort-asc');
    }

    rows.sort((a, b) => {
        const aValue = a.children[columnIndex].textContent.trim();
        const bValue = b.children[columnIndex].textContent.trim();

        if (!isNaN(aValue) && !isNaN(bValue)) {
            return isAscending ? aValue - bValue : bValue - aValue;
        }

        return isAscending
            ? aValue.localeCompare(bValue)
            : bValue.localeCompare(aValue);
    });

    tbody.innerHTML = '';
    rows.forEach(row => tbody.appendChild(row));
}

// =====================================================
// LOCAL STORAGE HELPERS
// =====================================================

const Storage = {
    set: (key, value) => {
        try {
            localStorage.setItem(key, JSON.stringify(value));
        } catch (e) {
            console.error('Storage error:', e);
        }
    },

    get: (key) => {
        try {
            const item = localStorage.getItem(key);
            return item ? JSON.parse(item) : null;
        } catch (e) {
            console.error('Storage error:', e);
            return null;
        }
    },

    remove: (key) => {
        try {
            localStorage.removeItem(key);
        } catch (e) {
            console.error('Storage error:', e);
        }
    },

    clear: () => {
        try {
            localStorage.clear();
        } catch (e) {
            console.error('Storage error:', e);
        }
    }
};

// =====================================================
// DEBUG HELPER
// =====================================================

const Debug = {
    log: (message, data) => {
        if (window.DEBUG_MODE) {
            console.log(`[FinSight AI] ${message}`, data || '');
        }
    },

    error: (message, data) => {
        console.error(`[FinSight AI] ${message}`, data || '');
    }
};

// Enable debug mode for development
window.DEBUG_MODE = false; // Set to true for debugging
