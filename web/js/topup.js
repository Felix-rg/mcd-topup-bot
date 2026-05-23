let products = [];
let selectedProvider = null;
let selectedSku = null;
let selectedItemName = null;
let selectedPrice = null;
let currentOrderId = null;
let confirmModal;

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

async function loadProducts() {
    try {
        const res = await fetch("/api/products");
        if (!res.ok) throw new Error("Gagal mengambil data produk");
        products = await res.json();
        renderGameList();
    } catch (e) {
        console.error("Error load products:", e);
    }
}

function getImageUrl(provider) {
    const key = provider.toLowerCase();
    for (let k in imageDb) { if (key.includes(k)) return imageDb[k]; }
    return imageDb["default"];
}

function renderGameList() {
    const container = document.getElementById("game-list");
    const providers = [...new Set(products.map(p => p.provider))];
    container.innerHTML = "";
    providers.forEach(p => {
        container.innerHTML += `
            <div class="col-6 col-md-4 col-lg-3">
                <div class="game-card" onclick="openGameOrder('${p}')">
                    <img src="${getImageUrl(p)}" alt="${p}">
                    <div class="game-title">${p}</div>
                </div>
            </div>`;
    });
}

function openGameOrder(provider) {
    selectedProvider = provider;
    selectedSku = null;
    document.getElementById("game-title").innerText = provider;
    document.getElementById("game-image").src = getImageUrl(provider);
    
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
    window.scrollTo(0, 0);
}

function renderNominals(provider) {
    const grid = document.getElementById("nominal-grid");
    grid.innerHTML = "";
    products.filter(p => p.provider === provider).forEach(p => {
        grid.innerHTML += `
            <div class="col-6 col-md-4">
                <div class="nominal-card" onclick="selectSku('${p.sku}', '${p.name}', ${p.price}, this)">
                    <div class="name">${p.name}</div>
                    <div class="price">Rp ${p.price.toLocaleString('id-ID')}</div>
                </div>
            </div>`;
    });
}

function selectSku(sku, name, price, element) {
    selectedSku = sku;
    selectedItemName = name;
    selectedPrice = price;
    document.querySelectorAll(".nominal-card").forEach(el => el.classList.remove("active"));
    element.classList.add("active");
}

function selectPayment(method, element) {
    document.getElementById("method").value = method;
    document.querySelectorAll(".payment-card").forEach(el => el.classList.remove("active"));
    element.classList.add("active");
}

let typingTimer;
function cekNicknameOtomatis() {
    clearTimeout(typingTimer);
    const uid = document.getElementById("user_id")?.value;
    
    if (!uid || uid.length < 4) {
        document.getElementById("nickname-box").style.display = "none";
        return;
    }

    document.getElementById("nickname-box").style.display = "block";
    document.getElementById("player-nickname").innerText = "Mencari data...";
    document.getElementById("player-nickname").className = "text-secondary fs-5";

    typingTimer = setTimeout(() => {
        const fakeName = "Player_" + uid.substring(0, 5); 
        document.getElementById("player-nickname").innerText = fakeName;
        document.getElementById("player-nickname").className = "text-success fw-bold fs-5";
        document.getElementById("player-nickname").dataset.name = fakeName;
    }, 1000);
}

function validasiSebelumBeli() {
    // 1. Cek dulu apakah pembeli udah milih produk
    if (!selectedSku) {
        Swal.fire("Pilih Produk", "Pilih nominal topup dulu bejir!", "warning");
        return;
    }

    // 2. Ambil Data ID Game (Biar masuk ke struk)
    const uid = document.getElementById("user_id")?.value;
    const zid = document.getElementById("zone_id")?.value;
    if (!uid) {
        Swal.fire("Data Kosong", "Masukkan ID Game kamu dulu!", "warning");
        return;
    }
    const accountId = zid ? `${uid} (${zid})` : uid;

    // 3. Hitung Biaya Admin Tripay (Pakai variabel selectedPrice yang bener)
    let adminFee = 0;
    const basePrice = selectedPrice; 
    const method = document.getElementById("method").value;

    if (method === "QRIS") {
        adminFee = Math.ceil(basePrice * 0.007); // QRIS 0.7%
    } else if (method === "OVO" || method === "DANA") {
        adminFee = Math.ceil(basePrice * 0.015); // E-Wallet 1.5%
    } else {
        adminFee = 4500; // VA atau Bank flat Rp 4.500
    }

    const totalPrice = basePrice + adminFee;

    // 4. Isi Data ke Modal Konfirmasi
    document.getElementById("conf-game").innerText = selectedProvider;
    document.getElementById("conf-id").innerText = accountId;
    
    // Tarik Nickname dari alert ijo
    const nickElement = document.getElementById("player-nickname");
    const nickname = (nickElement && nickElement.dataset.name) ? nickElement.dataset.name : "-";
    document.getElementById("conf-nick").innerText = nickname;

    document.getElementById("conf-item").innerText = selectedItemName;
    document.getElementById("conf-method").innerText = method;
    
    // Tampilkan rincian harga + Fee
    document.getElementById("conf-base-price").innerText = "Rp " + basePrice.toLocaleString('id-ID');
    document.getElementById("conf-fee").innerText = "+ Rp " + adminFee.toLocaleString('id-ID');
    document.getElementById("conf-price").innerText = "Rp " + totalPrice.toLocaleString('id-ID');

    // 5. Tampilkan Modalnya ke layar
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
        const s = (data.status || "").toLowerCase();
        
        if (s.includes("success")) { 
            if (currentPopupStatus !== "success") {
                Swal.fire({
                    title: 'Berhasil!',
                    text: 'Pesanan Anda telah masuk ke akun!',
                    icon: 'success',
                    confirmButtonText: 'Tutup'
                }).then(() => { location.reload(); }); // Langsung refresh kalau di-close
                currentPopupStatus = "success";
            }
            localStorage.removeItem("last_order_id");
            currentOrderId = null; 
        } else if (s.includes("failed")) {
            if (currentPopupStatus !== "failed") {
                Swal.fire({
                    title: 'Dibatalkan',
                    text: 'Pembayaran gagal atau telah kadaluarsa.',
                    icon: 'error',
                    confirmButtonText: 'Tutup'
                }).then(() => { location.reload(); });
                currentPopupStatus = "failed";
            }
            localStorage.removeItem("last_order_id");
            currentOrderId = null; 
        } 
        // 🛡️ PERBAIKAN BUG UNPAID DI SINI: Pake tanda === biar ngeceknya harus persis kata "paid"
        else if (s === "processing" || s === "paid") {
            showStatusResult("processing", data.qr_url, data.invoice_url); 
            setTimeout(updateStatusRealtime, 5000); 
        } else { 
            // Kalau UNPAID jatuhnya ke sini
            showStatusResult("pending", data.qr_url, data.invoice_url); 
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

    if (status === "pending") {
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
            allowEscapeKey: false
        });
    } else if (status === "processing") {
        Swal.fire({
            title: 'Pembayaran Diterima!',
            html: `
                <div class="spinner-border text-success my-3" role="status" style="width: 3rem; height: 3rem;"></div><br>
                <h5 class="text-success fw-bold mb-2">Sedang Mengirim Pesanan...</h5>
                <p class="small text-muted">Mohon tunggu sebentar, sistem sedang memproses topup ke ID Game Anda secara otomatis.</p>
            `,
            showConfirmButton: false,
            allowOutsideClick: false,
            allowEscapeKey: false
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