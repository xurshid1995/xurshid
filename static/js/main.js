// JavaScript funksiyalar

// Global fetch interceptor - 401 xatolik uchun va credentials qo'shish
const originalFetch = window.fetch;
window.fetch = function(...args) {
    // Agar options yo'q bo'lsa yoki credentials yo'q bo'lsa, qo'shish
    if (typeof args[1] !== 'object') {
        args[1] = {};
    }
    // Cookie yuborilishini ta'minlash
    if (!args[1].credentials) {
        args[1].credentials = 'same-origin';  // Cookie'larni har doim yuborish
    }
    
    return originalFetch.apply(this, args)
        .then(response => {
            // Agar 401 xatolik bo'lsa - avtomatik login sahifasiga yo'naltirish
            if (response.status === 401) {
                // Xabar ko'rsatish va login sahifasiga yo'naltirish
                console.warn('401 Unauthorized - Session tugagan, login sahifasiga yo\'naltirilmoqda...');
                
                // Alert ko'rsatish (foydalanuvchini xabardor qilish)
                setTimeout(() => {
                    alert('Session muddati tugadi. Iltimos, qayta login qiling.');
                    window.location.href = '/login';
                }, 100);
                
                // Response'ni qaytarish (catch'da xatolik ushlansa ham)
            }
            return response;
        });
};

// Sahifa yuklanganda ishga tushadi
document.addEventListener('DOMContentLoaded', function() {
    initializeApp();
});

// Appni boshlash
function initializeApp() {
    // API orqali mahsulotlarni yuklash
    loadProductsFromAPI();
    
    // Form validatsiyasini o'rnatish
    setupFormValidation();
}

// API orqali mahsulotlarni yuklash
async function loadProductsFromAPI() {
    try {
        const response = await fetch('/api/products');
        const products = await response.json();
        
        console.log('API orqali yuklangan mahsulotlar:', products);
        
        // Mahsulotlar sonini ko'rsatish
        updateProductCount(products.length);
        
    } catch (error) {
        console.error('Mahsulotlarni yuklashda xatolik:', error);
        showNotification('Mahsulotlarni yuklashda xatolik yuz berdi!', 'error');
    }
}

// Mahsulotni savatga qo'shish
function addToCart(productId) {
    console.log('Savatga qo\'shildi, mahsulot ID:', productId);
    
    // LocalStorage dan savatni olish
    let cart = JSON.parse(localStorage.getItem('cart')) || [];
    
    // Mahsulot allaqachon savatda bormi tekshirish
    const existingItem = cart.find(item => item.productId === productId);
    
    if (existingItem) {
        existingItem.quantity += 1;
    } else {
        cart.push({
            productId: productId,
            quantity: 1,
            addedAt: new Date().toISOString()
        });
    }
    
    // LocalStorage ga saqlash
    localStorage.setItem('cart', JSON.stringify(cart));
    
    showNotification('Mahsulot savatga qo\'shildi!', 'success');
    updateCartCount();
}

// Jami qiymatni hisoblash
async function calculateTotal() {
    try {
        const response = await fetch('/api/calculate');
        const data = await response.json();
        
        const totalDisplay = document.getElementById('total-display');
        const totalAmount = document.getElementById('total-amount');
        
        if (totalDisplay && totalAmount) {
            totalAmount.textContent = `$${data.total_value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
            totalDisplay.style.display = 'block';
            
            // Aniq qiymatni console ga chiqarish
            console.log('Aniq qiymat (Decimal):', data.precision);
            console.log('Float qiymat:', data.total_value);
        }
        
        showNotification('Jami qiymat hisoblandi!', 'success');
        
    } catch (error) {
        console.error('Jami qiymatni hisoblashda xatolik:', error);
        showNotification('Hisoblashda xatolik yuz berdi!', 'error');
    }
}

// Bildirishnoma ko'rsatish
function showNotification(message, type = 'info') {
    // Bildirishnoma elementini yaratish
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    
    // Stillarni qo'shish
    Object.assign(notification.style, {
        position: 'fixed',
        top: '20px',
        right: '20px',
        padding: '15px 20px',
        borderRadius: '5px',
        color: 'white',
        fontWeight: 'bold',
        zIndex: '1000',
        opacity: '0',
        transform: 'translateX(100%)',
        transition: 'all 0.3s ease-in-out'
    });
    
    // Turga qarab rangini o'rnatish
    switch(type) {
        case 'success':
            notification.style.backgroundColor = '#27ae60';
            break;
        case 'error':
            notification.style.backgroundColor = '#e74c3c';
            break;
        case 'warning':
            notification.style.backgroundColor = '#f39c12';
            break;
        default:
            notification.style.backgroundColor = '#3498db';
    }
    
    // DOMga qo'shish
    document.body.appendChild(notification);
    
    // Animatsiya bilan ko'rsatish
    setTimeout(() => {
        notification.style.opacity = '1';
        notification.style.transform = 'translateX(0)';
    }, 100);
    
    // 3 soniya keyin olib tashlash
    setTimeout(() => {
        notification.style.opacity = '0';
        notification.style.transform = 'translateX(100%)';
        
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 300);
    }, 3000);
}

// Form validatsiyasini o'rnatish
function setupFormValidation() {
    const priceInput = document.getElementById('price');
    
    if (priceInput) {
        priceInput.addEventListener('input', function() {
            const value = parseFloat(this.value);
            
            if (value < 0) {
                this.setCustomValidity('Narx manfiy bo\'lishi mumkin emas');
                showNotification('Narx manfiy bo\'lishi mumkin emas!', 'warning');
            } else if (value > 999999999.99) {
                this.setCustomValidity('Narx juda katta');
                showNotification('Narx juda katta!', 'warning');
            } else {
                this.setCustomValidity('');
            }
        });
        
        // Decimal formatni ko'rsatish
        priceInput.addEventListener('blur', function() {
            if (this.value) {
                const value = parseFloat(this.value);
                this.value = value.toFixed(2);
            }
        });
    }
}

// Mahsulotlar sonini yangilash
function updateProductCount(count) {
    const countElement = document.querySelector('.product-count');
    if (countElement) {
        countElement.textContent = `Jami: ${count} ta mahsulot`;
    }
}

// Savat sonini yangilash
function updateCartCount() {
    const cart = JSON.parse(localStorage.getItem('cart')) || [];
    const totalItems = cart.reduce((sum, item) => sum + item.quantity, 0);
    
    const cartCountElement = document.querySelector('.cart-count');
    if (cartCountElement) {
        cartCountElement.textContent = totalItems;
    }
}

// Decimal vs Float farqini ko'rsatish
function demonstrateDecimalPrecision() {
    console.group('🧮 Decimal vs Float aniqlik misoli');
    
    // JavaScript Float (aniq emas)
    const floatSum = 0.1 + 0.2;
    console.log('Float: 0.1 + 0.2 =', floatSum);
    console.log('Float aniq emas:', floatSum !== 0.3);
    
    // Python Decimal (aniq)
    console.log('Python Decimal: 0.1 + 0.2 = 0.3 (aniq)');
    console.log('PostgreSQL DECIMAL ham aniq natija beradi');
    
    console.groupEnd();
}

// API orqali yangi mahsulot qo'shish
async function addProductViaAPI(productData) {
    try {
        const response = await fetch('/api/products', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(productData)
        });
        
        if (response.ok) {
            const newProduct = await response.json();
            console.log('Yangi mahsulot qo\'shildi:', newProduct);
            showNotification('Mahsulot muvaffaqiyatli qo\'shildi!', 'success');
            return newProduct;
        } else {
            throw new Error('Mahsulot qo\'shishda xatolik');
        }
        
    } catch (error) {
        console.error('API xatoligi:', error);
        showNotification('Mahsulot qo\'shishda xatolik!', 'error');
        throw error;
    }
}

// Sahifa yuklanganda decimal misollari ko'rsatish
window.addEventListener('load', function() {
    demonstrateDecimalPrecision();
    updateCartCount();
});

// Global funksiyalar
window.addToCart = addToCart;
window.calculateTotal = calculateTotal;
window.addProductViaAPI = addProductViaAPI;

// ================================================
// MOBILE NAVIGATION FUNCTIONS
// ================================================

// Mobile navigation toggle with smooth animations
function toggleMobileNav() {
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.querySelector('.sidebar-overlay');
    const toggleBtn = document.querySelector('.mobile-nav-toggle');
    
    if (sidebar && overlay && toggleBtn) {
        const isActive = sidebar.classList.contains('active');
        
        if (isActive) {
            // Closing animation
            sidebar.classList.remove('active');
            overlay.classList.remove('active');
            toggleBtn.innerHTML = '<i class="fas fa-bars"></i>';
            document.body.style.overflow = '';
        } else {
            // Opening animation
            sidebar.classList.add('active');
            overlay.classList.add('active');
            toggleBtn.innerHTML = '<i class="fas fa-times"></i>';
            document.body.style.overflow = 'hidden';
        }
        
        // Add haptic feedback on mobile
        if ('vibrate' in navigator) {
            navigator.vibrate(50);
        }
    }
}

// Close mobile nav with animation
function closeMobileNav() {
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.querySelector('.sidebar-overlay');
    const toggleBtn = document.querySelector('.mobile-nav-toggle');
    
    if (sidebar && overlay && toggleBtn) {
        sidebar.classList.remove('active');
        overlay.classList.remove('active');
        toggleBtn.innerHTML = '<i class="fas fa-bars"></i>';
        document.body.style.overflow = '';
    }
}

// Mobile responsive table scroll
function enableMobileTableScroll() {
    const tables = document.querySelectorAll('table');
    
    tables.forEach(table => {
        // Table'ni wrapper ga o'rash
        if (!table.parentElement.classList.contains('table-responsive')) {
            const wrapper = document.createElement('div');
            wrapper.className = 'table-responsive';
            table.parentNode.insertBefore(wrapper, table);
            wrapper.appendChild(table);
        }
    });
}

// Window resize event - responsive behavior
window.addEventListener('resize', function() {
    if (window.innerWidth > 768) {
        closeMobileNav();
        // Desktop modega o'tganda mobile stillarni tozalash
        document.body.style.overflow = '';
    }
});

// Touch events for mobile - swipe to close
let touchStartX = 0;
let touchEndX = 0;

document.addEventListener('touchstart', function(e) {
    touchStartX = e.changedTouches[0].screenX;
    
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.querySelector('.sidebar-overlay');
    
    // Overlay click to close
    if (overlay && overlay.classList.contains('active') && e.target === overlay) {
        closeMobileNav();
    }
});

document.addEventListener('touchend', function(e) {
    touchEndX = e.changedTouches[0].screenX;
    handleSwipeGesture();
});

function handleSwipeGesture() {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar || !sidebar.classList.contains('active')) return;
    
    const swipeDistance = touchEndX - touchStartX;
    
    // Swipe left to close sidebar (at least 100px)
    if (swipeDistance < -100) {
        closeMobileNav();
    }
}

// Initialize mobile features
document.addEventListener('DOMContentLoaded', function() {
    enableMobileTableScroll();
    
    // Overlay click handler
    const overlay = document.querySelector('.sidebar-overlay');
    if (overlay) {
        overlay.addEventListener('click', closeMobileNav);
    }
});

// Global mobile functions
window.toggleMobileNav = toggleMobileNav;
window.closeMobileNav = closeMobileNav;
