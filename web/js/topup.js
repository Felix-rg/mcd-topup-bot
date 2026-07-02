let products = [];
let productCategories = [];
let providerIndex = {};
let selectedProvider = null;
let selectedSku = null;
let selectedItemName = null;
let selectedPrice = null;
let selectedProviderData = null;
let currentOrderId = null;
let confirmModal;
let activePromos = [];
let appliedPromoCode = null;

// VARIABEL BARU: Kunci Anti-Kedip
let currentPopupStatus = null; 

const imageDb = {
    "mobile legends": "https://placehold.co/400x400/1e1e2f/ffffff?text=Mobile+Legends",
    "free fire": "https://placehold.co/400x400/ff5500/ffffff?text=Free+Fire",
    "pubg": "https://placehold.co/400x400/ffcc00/000000?text=PUBG",
    "axis": "https://placehold.co/400x400/660099/ffffff?text=AXIS",
    "default": "https://placehold.co/400x400/cccccc/000000?text=GAME"
};

document.addEventListener("DOMContentLoaded", () => {
    confirmModal = new bootstrap.Modal(document.getElementById('confirmModal'));
    loadProducts();
    loadActivePromos();

    const lastOrderId = localStorage.getItem("last_order_id");
    console.log("🔥 CEK RADAR TAGIHAN: ", lastOrderId);

    if (lastOrderId) {
        currentOrderId = lastOrderId;
        currentPopupStatus = null; // Reset status popup
        
        Swal.fire({
            title: 'Melacak Tagihan...',
            html: '<div class="spinner-border text-primary my-3"></div><br><p>Tunggu sebentar ya boss...</p>',
            showConfirmButton: false,
            allowOutsideClick: false,
            allowEscapeKey: false
        });
        
        updateStatusRealtime(); 
    }
});

async function loadActivePromos() {
    try {
        const res = await fetch("/api/promos/active");
        if (!res.ok) return;
        const data = await res.json();
        activePromos = Array.isArray(data.promos) ? data.promos : [];
        renderPromoCampaigns();
    } catch (err) {
        console.error("Gagal memuat promo aktif:", err);
    }
}

function renderPromoCampaigns() {
    const container = document.getElementById("promoCampaignGrid");
    if (!container) return;

    if (!activePromos.length) {
        container.innerHTML = "";
        return;
    }

    const campaignPromos = activePromos.slice(0, 6);
    container.innerHTML = campaignPromos.map((promo) => {
        const badge = promo.badge || (promo.rule_type === "price" ? "Diskon" : "Info");
        const desc = promo.description || "Promo terbatas, cek detail dan periode promo sekarang.";
        const ctaText = (promo.cta_text || "Lihat Promo").trim();
        const ctaUrl = (promo.cta_url || "").trim();
        const ctaButton = ctaUrl
            ? `<button class="btn btn-sm btn-warning mt-2" onclick="handlePromoCta('${String(ctaUrl).replace(/'/g, "\\'")}')">${ctaText}</button>`
            : "";
        return `
            <div class="promo-campaign-card">
                <div>
                    <div class="promo-campaign-badge">${badge}</div>
                    <div class="promo-campaign-title">${promo.title || "Promo LIXAFA"}</div>
                    <div class="promo-campaign-desc">${desc}</div>
                </div>
                <div>
                    <div class="promo-campaign-meta">
                        <span>${promo.discount_type === "percent" ? `${promo.discount_value}% OFF` : (promo.discount_type === "fixed" ? `Potongan Rp ${Number(promo.discount_value || 0).toLocaleString('id-ID')}` : "Promo Spesial")}</span>
                        <span>${promo.target_scope ? `Target: ${promo.target_scope}` : ""}</span>
                    </div>
                    ${ctaButton}
                </div>
            </div>`;
    }).join("");
}

window.handlePromoCta = function(rawUrl) {
    const ctaUrl = String(rawUrl || "").trim();
    if (!ctaUrl) return;

    if (ctaUrl.startsWith("#")) {
        const sectionId = ctaUrl.slice(1);
        if (sectionId) {
            scrollToSection(sectionId);
        }
        return;
    }

    if (ctaUrl.startsWith("modal:")) {
        const modalId = ctaUrl.slice("modal:".length);
        const modalElement = document.getElementById(modalId);
        if (modalElement) {
            const modal = new bootstrap.Modal(modalElement);
            modal.show();
        }
        return;
    }

    if (ctaUrl.startsWith("game:")) {
        const provider = ctaUrl.slice("game:".length).trim();
        if (provider) {
            openGameOrder(provider);
        }
        return;
    }

    if (ctaUrl === "order" || ctaUrl === "/order") {
        const section = document.getElementById("produk-section");
        if (section) {
            showHome();
            section.scrollIntoView({ behavior: "smooth", block: "start" });
        }
        return;
    }

    if (ctaUrl.startsWith("http://") || ctaUrl.startsWith("https://") || ctaUrl.startsWith("/")) {
        window.open(ctaUrl, "_blank", "noopener");
        return;
    }

    const section = document.getElementById(ctaUrl);
    if (section) {
        scrollToSection(ctaUrl);
    }
}

async function loadProducts() {
    try {
        const res = await fetch("/api/products");
        if (!res.ok) throw new Error("Gagal mengambil data produk");
        const data = await res.json();
        if (Array.isArray(data)) {
            products = data;
            productCategories = [];
        } else {
            products = Array.isArray(data.products) ? data.products : [];
            productCategories = Array.isArray(data.categories) ? data.categories : [];
        }
        providerIndex = {};
        if (productCategories.length > 0) {
            productCategories.forEach(category => {
                (category.providers || []).forEach(provider => {
                    providerIndex[(provider.name || "").toLowerCase()] = provider;
                });
            });
        } else {
            const grouped = {};
            products.forEach(product => {
                const providerName = product.provider || "Lainnya";
                if (!grouped[providerName]) {
                    grouped[providerName] = {
                        name: providerName,
                        logo_url: product.logo_url || "",
                        image_url: product.image_url || "",
                        description: product.description || "",
                        promo_title: product.promo_title || "",
                        promo_text: product.promo_text || "",
                        promo_badge: product.promo_badge || "",
                        promo_url: product.promo_url || "",
                        items: [],
                    };
                }
                grouped[providerName].items.push(product);
            });
            Object.values(grouped).forEach(provider => {
                providerIndex[provider.name.toLowerCase()] = provider;
            });
        }
        renderGameList();
    } catch (e) {
        console.error("Error load products:", e);
    }
}

function normalizeUrl(url) {
    if (!url) return "";
    if (url.startsWith("http://") || url.startsWith("https://") || url.startsWith("/")) return url;
    return `/${url.replace(/^\/+/, "")}`;
}

function getProviderData(provider) {
    return providerIndex[(provider || "").toLowerCase()] || null;
}

function getImageUrl(provider) {
    const data = getProviderData(provider);
    if (data) {
        const candidate = data.logo_url || data.image_url;
        if (candidate) return normalizeUrl(candidate);
    }
    const key = (provider || "").toLowerCase();
    for (let k in imageDb) { if (key.includes(k)) return imageDb[k]; }
    return imageDb["default"];
}

function renderGameList() {
    const container = document.getElementById("game-list");
    container.innerHTML = "";

    if (productCategories.length > 0) {
        productCategories.forEach(category => {
            const providers = Array.isArray(category.providers) ? category.providers : [];
            if (!providers.length) return;

            container.innerHTML += `
                <div class="game-category-block">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <div>
                            <div class="text-uppercase small text-muted fw-bold">Kategori</div>
                            <h5 class="mb-0 text-white">${category.name}</h5>
                        </div>
                        <span class="badge rounded-pill text-bg-dark border border-warning-subtle">${providers.length} provider</span>
                    </div>
                    <div class="row g-3">
                        ${providers.map(provider => {
                            const heroImage = normalizeUrl(provider.logo_url || provider.image_url || getImageUrl(provider.name));
                            const badge = provider.promo_badge ? `<span class="game-badge">${provider.promo_badge}</span>` : "";
                            return `
                                <div class="col-6 col-md-4 col-lg-3">
                                    <div class="game-card" onclick="openGameOrder('${provider.name.replace(/'/g, "\\'")}')">
                                        <img src="${heroImage}" alt="${provider.name}">
                                        ${badge}
                                        <div class="game-title">${provider.name}</div>
                                    </div>
                                </div>`;
                        }).join("")}
                    </div>
                </div>`;
        });
        return;
    }

    const providers = [...new Set(products.map(p => p.provider))];
    container.innerHTML = `
        <div class="game-category-block">
            <div class="row g-3">
                ${providers.map(p => `
                    <div class="col-6 col-md-4 col-lg-3">
                        <div class="game-card" onclick="openGameOrder('${p.replace(/'/g, "\\'")}')">
                            <img src="${getImageUrl(p)}" alt="${p}">
                            <div class="game-title">${p}</div>
                        </div>
                    </div>`).join("")}
            </div>
        </div>`;
}

function openGameOrder(provider) {
    selectedProvider = provider;
    selectedProviderData = getProviderData(provider);
    selectedSku = null;
    document.getElementById("game-title").innerText = provider;
    document.getElementById("game-image").src = getImageUrl(provider);
    const gameMeta = document.getElementById("game-meta");
    if (gameMeta) {
        gameMeta.innerText = selectedProviderData?.description || "";
        gameMeta.style.display = selectedProviderData?.description ? "block" : "none";
    }
    
    const inputContainer = document.getElementById("dynamic-input-container");
    if (provider.toLowerCase().includes("mobile legends")) {
        inputContainer.innerHTML = `
            <div class="col-7"><input type="number" id="user_id" class="form-control form-control-lg bg-light" placeholder="User ID" oninput="cekNicknameOtomatis()"></div>
            <div class="col-5"><input type="number" id="zone_id" class="form-control form-control-lg bg-light" placeholder="Zone ID" oninput="cekNicknameOtomatis()"></div>
        `;
    } else {
        inputContainer.innerHTML = `
            <div class="col-12"><input type="text" id="user_id" class="form-control form-control-lg bg-light" placeholder="Masukkan ID Game" oninput="cekNicknameOtomatis()"></div>
        `;
    }
    
    document.getElementById("nickname-box").style.display = "none";
    document.getElementById("player-nickname").innerText = "";
    document.getElementById("home-view").style.display = "none";
    document.getElementById("order-view").style.display = "block";
    window.scrollTo(0, 0);
    renderNominals(provider);
}

function showHome() {
    document.getElementById("order-view").style.display = "none";
    document.getElementById("home-view").style.display = "block";
    appliedPromoCode = null;
    window.scrollTo(0, 0);
}

function renderNominals(provider) {
    const grid = document.getElementById("nominal-grid");
    grid.innerHTML = "";
    const items = selectedProviderData?.items || products.filter(p => p.provider === provider);
    items.forEach(p => {
        const effectivePrice = Number(p.price || 0);
        const originalPrice = Number(p.original_price || p.price || 0);
        const hasDiscount = originalPrice > effectivePrice;
        const promoBadge = p.promo_applied?.badge || p.promo_badge || "";
        grid.innerHTML += `
            <div class="col-6 col-md-4">
                <div class="nominal-card" onclick="selectSku('${p.sku}', '${p.name}', ${effectivePrice}, ${originalPrice}, this)">
                    <div class="name">${p.name}</div>
                    ${hasDiscount ? `<div class="small text-decoration-line-through fw-semibold" style="color:#ff4d4f;">Rp ${originalPrice.toLocaleString('id-ID')}</div>` : ""}
                    <div class="price">Rp ${effectivePrice.toLocaleString('id-ID')}</div>
                    ${hasDiscount && promoBadge ? `<div class="small text-warning fw-semibold mt-1">${promoBadge}</div>` : ""}
                </div>
            </div>`;
    });
}

function selectSku(sku, name, price, originalPrice, element) {
    selectedSku = sku;
    selectedItemName = name;
    selectedPrice = price;
    window.selectedOriginalPrice = originalPrice;
    document.querySelectorAll(".nominal-card").forEach(el => el.classList.remove("active"));
    element.classList.add("active");

    // Ganti nominal harus re-apply manual agar validasi promo tidak otomatis.
    appliedPromoCode = null;
    const feedback = document.getElementById("promo_code_feedback");
    if (feedback) {
        feedback.innerText = "";
        feedback.style.color = "var(--muted)";
    }
}

window.applyPromoCode = async function() {
    const feedback = document.getElementById("promo_code_feedback");
    const promoCodeInput = document.getElementById("promo_code");
    const code = promoCodeInput?.value?.trim() || "";

    if (!selectedSku) {
        if (feedback) {
            feedback.innerText = "Pilih nominal dulu sebelum apply promo.";
            feedback.style.color = "#ff4d4f";
        }
        return;
    }

    if (!code) {
        appliedPromoCode = null;
        if (feedback) {
            feedback.innerText = "";
            feedback.style.color = "var(--muted)";
        }
        return;
    }

    try {
        const res = await fetch(`/api/promos/validate?sku=${encodeURIComponent(selectedSku)}&code=${encodeURIComponent(code)}`);
        const data = await res.json();
        if (!res.ok) {
            appliedPromoCode = null;
            if (feedback) {
                feedback.innerText = data.detail || "Kode promo tidak valid.";
                feedback.style.color = "#ff4d4f";
            }
            return;
        }

        appliedPromoCode = code;
        selectedPrice = Number(data.final_price || selectedPrice || 0);
        window.selectedOriginalPrice = Number(data.original_price || selectedPrice || 0);
        if (feedback) {
            feedback.innerText = `Promo aktif. Hemat Rp ${Number(data.discount_amount || 0).toLocaleString('id-ID')}.`;
            feedback.style.color = "#3CB371";
        }
    } catch (err) {
        appliedPromoCode = null;
        if (feedback) {
            feedback.innerText = "Gagal memvalidasi kode promo.";
            feedback.style.color = "#ff4d4f";
        }
    }
}

function selectPayment(method, element) {
    document.getElementById("method").value = method;
    document.querySelectorAll(".payment-option-txn").forEach(el => el.classList.remove("active"));
    element.classList.add("active");
}

let typingTimer;
function cekNicknameOtomatis() {
    clearTimeout(typingTimer);
    const uid = document.getElementById("user_id")?.value;
    const zid = document.getElementById("zone_id")?.value || "";
    // Ambil nama provider (game) yang sedang dipilih user
    const gameCode = selectedProvider || "game"; 

    if (!uid || uid.length < 4) {
        document.getElementById("nickname-box").style.display = "none";
        // Kosongkan dataset jika ID dihapus
        const nickElement = document.getElementById("player-nickname");
        if (nickElement) nickElement.dataset.name = "";
        return;
    }

    document.getElementById("nickname-box").style.display = "block";
    document.getElementById("player-nickname").innerText = "Mencari data ke server...";
    document.getElementById("player-nickname").className = "text-secondary fs-5";

    typingTimer = setTimeout(async () => {
        try {
            // Memanggil API Backend yang kita buat di topup_routes.py
            const res = await fetch("/check-nickname", { 
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ 
                    game_code: gameCode, 
                    user_id: uid, 
                    zone_id: zid 
                })
            });
            
            const data = await res.json();

            if (!res.ok) {
                // Jika API membalas ID tidak ditemukan / error
                document.getElementById("player-nickname").innerText = "ID Tidak Ditemukan";
                document.getElementById("player-nickname").className = "text-danger fw-bold fs-5";
                document.getElementById("player-nickname").dataset.name = ""; // Kosongkan dataset
            } else {
                // Jika Nickname berhasil ditemukan
                document.getElementById("player-nickname").innerText = data.nickname;
                document.getElementById("player-nickname").className = "text-success fw-bold fs-5";
                document.getElementById("player-nickname").dataset.name = data.nickname; // Simpan untuk struk
            }
        } catch (err) {
            document.getElementById("player-nickname").innerText = "Gagal menghubungi server";
            document.getElementById("player-nickname").className = "text-danger fw-bold fs-5";
            document.getElementById("player-nickname").dataset.name = "";
        }
    }, 1000); // Tunggu user selesai mengetik 1 detik sebelum hit API
}

function validasiSebelumBeli() {
    // 1. Cek dulu apakah pembeli udah milih produk
    if (!selectedSku) {
        Swal.fire("Pilih Produk", "Pilih nominal topup dulu bejir!", "warning");
        return;
    }

    // 2. Ambil Data ID Game
    const uid = document.getElementById("user_id")?.value;
    const zid = document.getElementById("zone_id")?.value;
    if (!uid) {
        Swal.fire("Data Kosong", "Masukkan ID Game kamu dulu!", "warning");
        return;
    }
    const accountId = zid ? `${uid} (${zid})` : uid;

    // 3. CEK APAKAH NICKNAME VALID SEBELUM MELANJUTKAN (PERBAIKAN KRUSIAL)
    const nickElement = document.getElementById("player-nickname");
    const nickname = (nickElement && nickElement.dataset.name) ? nickElement.dataset.name : "";
    
    if (!nickname) {
        Swal.fire("ID Tidak Valid", "Pastikan User ID benar dan Nama Akun sudah muncul di layar sebelum melanjutkan pembayaran.", "error");
        return; // Hentikan eksekusi, modal tidak akan muncul!
    }

    // 4. Hitung Biaya Admin Tripay
    let adminFee = 0;
    const basePrice = selectedPrice; 
    const originalPrice = Number(window.selectedOriginalPrice || selectedPrice || 0);
    const method = document.getElementById("method").value;

    if (method === "QRIS") {
        adminFee = Math.ceil(basePrice * 0.007); // QRIS 0.7%
    } else if (method === "OVO" || method === "DANA") {
        adminFee = Math.ceil(basePrice * 0.015); // E-Wallet 1.5%
    } else {
        adminFee = 4500; // VA atau Bank flat Rp 4.500
    }

    const totalPrice = basePrice + adminFee;

    // 5. Isi Data ke Modal Konfirmasi
    document.getElementById("conf-game").innerText = selectedProvider;
    document.getElementById("conf-id").innerText = accountId;
    document.getElementById("conf-nick").innerText = nickname; // Nickname ditaruh di sini
    document.getElementById("conf-item").innerText = selectedItemName;
    document.getElementById("conf-method").innerText = method;
    
    // Tampilkan rincian harga + Fee
    if (originalPrice > basePrice) {
        document.getElementById("conf-base-price").innerText = `Rp ${basePrice.toLocaleString('id-ID')} (Normal: Rp ${originalPrice.toLocaleString('id-ID')})`;
    } else {
        document.getElementById("conf-base-price").innerText = "Rp " + basePrice.toLocaleString('id-ID');
    }
    document.getElementById("conf-fee").innerText = "+ Rp " + adminFee.toLocaleString('id-ID');
    document.getElementById("conf-price").innerText = "Rp " + totalPrice.toLocaleString('id-ID');

    // 6. Tampilkan Modalnya ke layar
    var modal = new bootstrap.Modal(document.getElementById('confirmModal'));
    modal.show();
}

async function eksekusiBeli() {
    const btn = document.getElementById("btnEksekusi");
    btn.disabled = true;
    btn.innerText = "Memproses...";

    const uid = document.getElementById("user_id")?.value;
    const zid = document.getElementById("zone_id")?.value;
    const wa_pembeli = document.getElementById("wa_pembeli")?.value || "080000000000";
    
    const target_id = zid ? `${uid}${zid}` : uid; 
    const method = document.getElementById("method").value;
    const nickname = document.getElementById("player-nickname").dataset.name || "-";

    try {
        const res = await fetch("/topup", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ 
                phone: wa_pembeli,
                target_id: target_id,
                provider: selectedProvider, 
                nominal: selectedSku, 
                promo_code: appliedPromoCode,
                nickname: nickname,
                method 
            })
        });
        
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail);

        currentOrderId = data.id;
        localStorage.setItem("last_order_id", currentOrderId);
        currentPopupStatus = null; // Reset status popup
        
        confirmModal.hide();
        
        Swal.fire({
            title: 'Menghubungkan ke Tripay...',
            html: '<div class="spinner-border text-primary my-3"></div>',
            showConfirmButton: false,
            allowOutsideClick: false,
            allowEscapeKey: false
        });

        updateStatusRealtime(); 
        
    } catch (err) {
        Swal.fire("Gagal", err.message, "error");
    } finally {
        btn.disabled = false;
        btn.innerText = "BAYAR SEKARANG";
    }
}

async function updateStatusRealtime() {
    if (!currentOrderId) return;
    try {
        const res = await fetch("/topup/" + currentOrderId);
        
        if (!res.ok) {
            if (currentPopupStatus !== "error") {
                Swal.fire('Error', 'Data transaksi hilang dari server!', 'error');
                currentPopupStatus = "error";
            }
            localStorage.removeItem('last_order_id');
            return; 
        }

        const data = await res.json();
        const stage = (data.stage || "pending_payment").toLowerCase();
        
        if (stage === "success") { 
            if (currentPopupStatus !== "success") {
                Swal.fire({
                    title: 'Berhasil!',
                    text: 'Pesanan Anda telah masuk ke akun!',
                    icon: 'success',
                    background: '#111111',
                    color: '#f5f5f5',
                    confirmButtonColor: '#C19B32',
                    confirmButtonText: 'Tutup'
                }).then(() => { location.reload(); }); // Langsung refresh kalau di-close
                currentPopupStatus = "success";
            }
            localStorage.removeItem("last_order_id");
            currentOrderId = null; 
        } else if (stage === "failed") {
            if (currentPopupStatus !== "failed") {
                Swal.fire({
                    title: 'Dibatalkan',
                    text: 'Pembayaran gagal atau telah kadaluarsa.',
                    icon: 'error',
                    background: '#111111',
                    color: '#f5f5f5',
                    confirmButtonColor: '#C19B32',
                    confirmButtonText: 'Tutup'
                }).then(() => { location.reload(); });
                currentPopupStatus = "failed";
            }
            localStorage.removeItem("last_order_id");
            currentOrderId = null; 
        } else if (stage === "processing") {
            showStatusResult("processing", data.qr_url, data.invoice_url); 
            setTimeout(updateStatusRealtime, 5000); 
        } else if (stage === "provider_pending") {
            showStatusResult("provider_pending", data.qr_url, data.invoice_url); 
            setTimeout(updateStatusRealtime, 5000);
        } else if (stage === "pending_payment") {
            showStatusResult("pending_payment", data.qr_url, data.invoice_url); 
            setTimeout(updateStatusRealtime, 5000); 
        } else {
            showStatusResult("processing", data.qr_url, data.invoice_url);
            setTimeout(updateStatusRealtime, 5000);
        }
    } catch(e) { 
        setTimeout(updateStatusRealtime, 5000); 
    }
}

function showStatusResult(status, qrUrl, invoiceUrl) {
    // 🛡️ KUNCI ANTI-KEDIP: Kalau statusnya masih sama kayak 5 detik lalu, DIAM AJA!
    if (currentPopupStatus === status) return; 
    currentPopupStatus = status; // Kalau status baru, catat statusnya!

    if (status === "pending_payment") {
        Swal.fire({
            title: 'Selesaikan Pembayaran',
            html: `
                <div class="spinner-border text-primary my-3" role="status" style="width: 3rem; height: 3rem;"></div><br>
                <a href="${invoiceUrl}" target="_blank" class="btn btn-primary rounded-pill px-4 py-3 mt-3 fw-bold shadow-lg w-100" style="text-decoration: none; font-size: 1.1rem;">
                    <i class="bi bi-wallet2"></i> BUKA HALAMAN PEMBAYARAN
                </a>
                <button onclick="batalkanTransaksi()" class="btn btn-outline-danger rounded-pill px-4 py-2 mt-3 fw-bold w-100">
                    <i class="bi bi-x-circle"></i> Batalkan & Buat Pesanan Baru
                </button>
                <p class="small text-muted mt-3">Popup ini akan otomatis berubah jika Anda sudah membayar.</p>
            `,
            showConfirmButton: false,
            allowOutsideClick: false,
            allowEscapeKey: false,
            background: '#111111',
            color: '#f5f5f5'
        });
    } else if (status === "processing") {
        Swal.fire({
            title: 'Menunggu Pesanan Dikirim',
            html: `
                <div class="spinner-border text-success my-3" role="status" style="width: 3rem; height: 3rem;"></div><br>
                <h5 class="text-success fw-bold mb-2">Pembayaran diterima, sistem sedang menembak provider...</h5>
                <p class="small text-muted">Mohon tunggu sebentar, pesanan sedang dikirim ke provider dan akan otomatis berubah jika sudah masuk.</p>
            `,
            showConfirmButton: false,
            allowOutsideClick: false,
            allowEscapeKey: false,
            background: '#111111',
            color: '#f5f5f5'
        });
    } else if (status === "provider_pending") {
        Swal.fire({
            title: 'Menunggu Provider',
            html: `
                <div class="spinner-border text-warning my-3" role="status" style="width: 3rem; height: 3rem;"></div><br>
                <h5 class="text-warning fw-bold mb-2">Pesanan sudah dikirim ke provider</h5>
                <p class="small text-muted">Sekarang sistem menunggu konfirmasi dari provider. Silakan tunggu, status akan berubah otomatis saat sukses.</p>
            `,
            showConfirmButton: false,
            allowOutsideClick: false,
            allowEscapeKey: false,
            background: '#111111',
            color: '#f5f5f5'
        });
    } else if (status === "pending_payment") {
        Swal.fire({
            title: 'Selesaikan Pembayaran',
            html: `
                <div class="spinner-border text-primary my-3" role="status" style="width: 3rem; height: 3rem;"></div><br>
                <a href="${invoiceUrl}" target="_blank" class="btn btn-primary rounded-pill px-4 py-3 mt-3 fw-bold shadow-lg w-100" style="text-decoration: none; font-size: 1.1rem;">
                    <i class="bi bi-wallet2"></i> BUKA HALAMAN PEMBAYARAN
                </a>
                <button onclick="batalkanTransaksi()" class="btn btn-outline-danger rounded-pill px-4 py-2 mt-3 fw-bold w-100">
                    <i class="bi bi-x-circle"></i> Batalkan & Buat Pesanan Baru
                </button>
                <p class="small text-muted mt-3">Popup ini akan otomatis berubah jika Anda sudah membayar.</p>
            `,
            showConfirmButton: false,
            allowOutsideClick: false,
            allowEscapeKey: false,
            background: '#111111',
            color: '#f5f5f5'
        });
    }
}

// FITUR BARU: BATALKAN TRANSAKSI
// FITUR BARU: BATALKAN TRANSAKSI (SAMPAI KE DATABASE)
window.batalkanTransaksi = function() {
    Swal.fire({
        title: 'Batalkan Pesanan?',
        text: "Anda yakin ingin membatalkan tagihan ini dan memilih nominal lain?",
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#d33',
        cancelButtonColor: '#6c757d',
        confirmButtonText: 'Ya, Batalkan!',
        cancelButtonText: 'Kembali'
    }).then(async (result) => { // PERBAIKAN: Tambah async di sini
        if (result.isConfirmed) {
            
            // 1. LAPOR KE SERVER BUAT GANTI STATUS DATABASE JADI CANCELED
            if (currentOrderId) {
                try {
                    await fetch(`/topup/${currentOrderId}/cancel`, { method: 'POST' });
                } catch(e) { console.error("Gagal lapor ke server", e); }
            }

            // 2. Hapus ingatan tagihan dari browser
            localStorage.removeItem("last_order_id");
            currentOrderId = null;
            
            Swal.fire({
                title: 'Dibatalkan!',
                text: 'Pesanan telah dibatalkan.',
                icon: 'success',
                timer: 1500,
                showConfirmButton: false
            }).then(() => {
                location.reload(); // Refresh halaman biar bersih total
            });
        } else {
            // Kalau gajadi batal, pancing popup tagihannya biar muncul lagi
            currentPopupStatus = null; 
            updateStatusRealtime();
        }
    });
}

// FITUR CEK PESANAN
async function cekStatusPesanan() {
    const identifier = document.getElementById("input_cek_pesanan").value.trim();
    const resultBox = document.getElementById("hasil_cek_pesanan");

    if (!identifier) {
        alert("Masukkan Nomor HP atau Order ID dulu bejir!");
        return;
    }

    // Kasih efek loading biar keren
    resultBox.classList.remove("d-none");
    resultBox.innerHTML = `<div class="text-center"><div class="spinner-border text-warning spinner-border-sm"></div> Mencari data...</div>`;

    try {
        const response = await fetch(`/topup/${identifier}`);
        const data = await response.json();

        if (!response.ok) {
            resultBox.innerHTML = `<div class="text-danger fw-bold"><i class="bi bi-x-circle"></i> ${data.detail || "Pesanan tidak ditemukan"}</div>`;
            return;
        }

        // Tentukan warna badge status
        let badgeColor = "bg-secondary";
        let statusText = data.status;
        
        if (statusText === "SUCCESS") badgeColor = "bg-success";
        else if (statusText === "UNPAID") badgeColor = "bg-danger";
        else if (statusText === "PAID" || statusText === "PROCESSING") {
            badgeColor = "bg-warning text-dark";
            statusText = "DIPROSES";
        } else if (statusText === "FAILED") badgeColor = "bg-dark border border-danger text-danger";

        // Susun tampilan hasil
        let htmlResult = `
            <div class="d-flex justify-content-between align-items-center border-bottom border-dark pb-2 mb-2">
                <span class="small text-muted">Status:</span>
                <span class="badge ${badgeColor}">${statusText}</span>
            </div>
        `;

        // Kalau belum dibayar, kasih tombol ke Tripay
        if (data.status === "UNPAID" && data.invoice_url) {
            htmlResult += `
                <div class="mt-3 text-center">
                    <p class="small mb-2">Pesanan belum dibayar, silakan selesaikan pembayaran:</p>
                    <a href="${data.invoice_url}" target="_blank" class="btn btn-sm btn-primary w-100 fw-bold">Bayar Sekarang</a>
                </div>
            `;
        }

        resultBox.innerHTML = htmlResult;

    } catch (error) {
        resultBox.innerHTML = `<div class="text-danger fw-bold">Gagal menghubungi server.</div>`;
    }
}