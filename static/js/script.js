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

// =====================================================
// FIN SIGHT AI ASSISTANT
// =====================================================
document.addEventListener('DOMContentLoaded', () => {
    const shell = document.getElementById('finsightChat');
    if (!shell) return;
    const toggle = document.getElementById('chatToggle');
    const close = document.getElementById('chatClose');
    const windowEl = document.getElementById('chatWindow');
    const messages = document.getElementById('chatMessages');
    const form = document.getElementById('chatForm');
    const input = document.getElementById('chatInput');
    const send = document.getElementById('chatSend');
    const clear = document.getElementById('chatClear');
    const suggestions = document.querySelectorAll('.chat-suggestion');
    const token = document.querySelector('meta[name="csrf-token"]')?.content || '';

    const addMessage = (role, content, temporary = false) => {
        const item = document.createElement('div');
        item.className = `chat-message ${role}${temporary ? ' chat-thinking' : ''}`;
        if (role.includes('error')) {
            const text = document.createElement('span');
            text.className = 'chat-error-text';
            text.textContent = content;
            const dismiss = document.createElement('button');
            dismiss.type = 'button';
            dismiss.className = 'chat-error-close';
            dismiss.setAttribute('aria-label', shell.dataset.errorClose || 'Close');
            dismiss.title = shell.dataset.errorClose || 'Close';
            dismiss.innerHTML = '<i class="fas fa-xmark" aria-hidden="true"></i>';
            dismiss.addEventListener('click', () => item.remove());
            item.append(text, dismiss);
        } else {
            item.textContent = content;
        }
        messages.appendChild(item);
        messages.scrollTop = messages.scrollHeight;
        return item;
    };
    const setOpen = open => {
        windowEl.classList.toggle('d-none', !open);
        toggle.setAttribute('aria-expanded', String(open));
        if (open) input.focus();
    };
    toggle.addEventListener('click', () => setOpen(windowEl.classList.contains('d-none')));
    close.addEventListener('click', () => setOpen(false));
    suggestions.forEach(button => button.addEventListener('click', () => {
        input.value = button.dataset.question || button.textContent.trim();
        form.requestSubmit();
    }));
    input.addEventListener('keydown', event => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        const question = input.value.trim();
        if (!question || send.disabled) return;
        addMessage('user', question);
        input.value = '';
        send.disabled = true;
        suggestions.forEach(button => { button.disabled = true; });
        const thinking = addMessage('assistant', shell.dataset.thinking || 'Analyzing...', true);
        try {
            const response = await fetch(shell.dataset.chatUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
                body: JSON.stringify({ message: question })
            });
            const data = await response.json().catch(() => ({}));
            thinking.remove();
            if (!response.ok) throw new Error(data.error || shell.dataset.error || 'The assistant could not answer.');
            addMessage('assistant', data.message || shell.dataset.error || 'The assistant returned no answer.');
        } catch (error) {
            thinking.remove();
            addMessage('assistant error', error.message || shell.dataset.error || 'The assistant could not answer.');
        } finally {
            send.disabled = false;
            suggestions.forEach(button => { button.disabled = false; });
            input.focus();
        }
    });
    clear.addEventListener('click', async () => {
        try {
            await fetch(shell.dataset.resetUrl, { method: 'POST', headers: { 'X-CSRFToken': token } });
        } catch (error) {
            Debug.log('Could not reset assistant context', error);
        }
        messages.innerHTML = '';
        addMessage('assistant', shell.dataset.welcome || document.querySelector('[data-chat-welcome]')?.textContent || '');
    });
});
