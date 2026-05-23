const token = localStorage.getItem("admin_token");
if(!token){ window.location.href="/admin"; }

let addModal, editModal;
let allGroupedProducts = {}; // Simpan data produk di sini

async function api(url, options = {}) {
    options.headers = { ...options.headers, "token": token };
    const res = await fetch(url, options);
    return await res.json();
}

function setActiveMenu(menuId) {
    document.querySelectorAll('.sidebar .nav-link').forEach(el => el.classList.remove('active'));
    document.getElementById(menuId).classList.add('active');
}

// --- FUNGSI LOAD DATA STATISTIK ---
window.loadStats = async function() {
    try {
        let r1 = await api("/admin/api/revenue-today");
        let r2 = await api("/admin/api/revenue-total");
        let r3 = await api("/admin/api/profit-today");
        let r4 = await api("/admin/api/profit-total");

        document.getElementById("revenue_today").innerText = "Rp " + r1.revenue.toLocaleString('id-ID');
        document.getElementById("revenue_total").innerText = "Rp " + r2.revenue.toLocaleString('id-ID');
        document.getElementById("profit_today").innerText = "Rp " + r3.profit.toLocaleString('id-ID');
        document.getElementById("profit_total").innerText = "Rp " + r4.profit.toLocaleString('id-ID');
    } catch (error) { console.error("Gagal load statistik:", error); }
}

// --- MENU TRANSAKSI ---
window.loadOrders = async function() {
    setActiveMenu('nav-orders');
    document.getElementById("page-title").innerText = "Data Transaksi Terakhir";
    document.getElementById("product_actions").style.display = "none";
    if(document.getElementById("category_nav")) document.getElementById("category_nav").style.display = "none";
    
    document.getElementById("order_container").style.display = "block";
    if(document.getElementById("product_container")) document.getElementById("product_container").style.display = "none";

    document.getElementById("main_table").innerHTML = "<tr><td class='text-center'>Loading...</td></tr>";

    try {
        let data = await api("/admin/api/orders");
        let html = `
        <thead class="table-light">
            <tr><th>ID Order</th><th>No. HP</th><th>SKU</th><th>Pembayaran</th><th>Status</th><th>Waktu</th></tr>
        </thead><tbody>`;

        data.forEach(o => {
            let badgePay = o.payment_status === "PAID" ? "bg-success" : "bg-warning text-dark";
            let badgeTop = o.topup_status === "SUCCESS" ? "bg-success" : (o.topup_status === "FAILED" ? "bg-danger" : "bg-info text-dark");
            html += `
            <tr>
                <td class="text-muted"><small>${o.id.substring(0,8)}</small></td>
                <td class="fw-bold">${o.phone}</td>
                <td><span class="badge bg-secondary">${o.nominal}</span></td>
                <td><span class="badge ${badgePay}">${o.payment_status}</span></td>
                <td><span class="badge ${badgeTop}">${o.topup_status}</span></td>
                <td><small class="text-muted">${new Date(o.created_at).toLocaleString()}</small></td>
            </tr>`;
        });
        document.getElementById("main_table").innerHTML = html + "</tbody>";
    } catch (e) { console.error(e); }
}

// --- FITUR PROFIT MASSAL ---
// --- FITUR PROFIT MASSAL (VERSI DROPDOWN AUTOMATIC + ANTI UNAUTHORIZED) ---
window.showBulkMarkupModal = async function() {
    const selectElement = document.getElementById("bulk_brand");
    selectElement.innerHTML = `<option value="">Mengecek database...</option>`;

    // Tampilkan modalnya dulu
    var modalElement = document.getElementById('bulkMarkupModal');
    var modalInstance = bootstrap.Modal.getInstance(modalElement) || new bootstrap.Modal(modalElement);
    modalInstance.show();

    try {
        // Narik semua produk dari database buat kita ambil nama provider uniknya
        const res = await api('/api/products'); 
        
        if (res && res.length > 0) {
            // Ambil nama provider aja, ubah ke huruf besar, terus ilangin yang dobel
            const providers = [...new Set(res.map(p => p.provider.toUpperCase()))].sort();
            
            // Masukin pilihan ALL di paling atas dropdown
            let optionsHtml = `<option value="ALL">✨ SEMUA PRODUK (ALL) ✨</option>`;
            
            // Masukin list provider yang ada di database lu satu-satu
            providers.forEach(prov => {
                optionsHtml += `<option value="${prov}">${prov}</option>`;
            });
            
            selectElement.innerHTML = optionsHtml;
        } else {
            selectElement.innerHTML = `<option value="">Gagal memuat atau database kosong</option>`;
        }
    } catch (error) {
        console.error("Gagal bikin list provider:", error);
        selectElement.innerHTML = `<option value="">Gagal memonitor database</option>`;
    }
};

window.saveBulkMarkup = async function() {
    const brand = document.getElementById("bulk_brand").value;
    const percent = parseFloat(document.getElementById("bulk_percent").value);
    const minProfit = parseInt(document.getElementById("bulk_min_profit").value) || 0;

    if (!brand) {
        alert("Pilih provider-nya dulu bejir!");
        return;
    }
    if (isNaN(percent) || percent <= 0) {
        alert("Masukkan angka persentase profit yang valid (misal: 5)!");
        return;
    }

    const btn = document.querySelector("#bulkMarkupModal .btn-warning");
    const originalText = btn.innerHTML;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Memproses...`;
    btn.disabled = true;

    try {
        const res = await api('/admin/bulk-markup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                brand: brand, 
                percent: percent,
                min_profit: minProfit
            })
        });

        if (res && res.message) {
            alert(res.message);
            const modalElement = document.getElementById('bulkMarkupModal');
            const modalInstance = bootstrap.Modal.getInstance(modalElement);
            if (modalInstance) modalInstance.hide();
            
            if (typeof loadProducts === 'function') loadProducts(); 
        } else {
            alert(res.error || res.detail || "Gagal melakukan update harga massal");
        }
    } catch (error) {
        console.error("Error Set Profit:", error);
        alert("Terjadi kesalahan sistem atau sesi admin habis.");
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
};

// --- MENU PRODUK (LOGIKA GROUPING BARU) ---
window.loadProducts = async function() {
    setActiveMenu('nav-products');
    document.getElementById("page-title").innerText = "Manajemen Produk";
    document.getElementById("product_actions").style.display = "block";
    document.getElementById("category_nav").style.display = "block";
    document.getElementById("order_container").style.display = "none";
    document.getElementById("product_container").style.display = "block";

    document.getElementById("product_container").innerHTML = "<div class='text-center p-5'><div class='spinner-border text-primary'></div><br>Memuat Produk...</div>";

    try {
        const res = await api("/admin/api/products");
        allGroupedProducts = res; 

        // --- LOGIKA OTOMATIS MULAI DI SINI ---
        
        // 1. Ambil semua kategori yang ada di data (Object Keys)
        const categories = Object.keys(allGroupedProducts);
        
        // 2. Ambil container tempat tombol tab berada
        const navContainer = document.querySelector("#category_nav ul");
        navContainer.innerHTML = ""; // Bersihkan tombol lama

        if (categories.length === 0) {
            document.getElementById("product_container").innerHTML = "<div class='alert alert-info'>Belum ada produk di database.</div>";
            return;
        }

        // 3. Bikin tombol tab otomatis buat setiap kategori
        categories.forEach((cat, index) => {
            const isActive = index === 0 ? 'active' : ''; // Tab pertama otomatis nyala
            navContainer.innerHTML += `
                <li class="nav-item">
                    <button class="nav-link ${isActive}" onclick="switchCategory('${cat}', this)">
                        ${cat}
                    </button>
                </li>`;
        });

        // 4. Tampilkan isi kategori pertama secara default
        renderByKategori(categories[0]);

    } catch (e) { 
        document.getElementById("product_container").innerHTML = "<div class='alert alert-danger'>Gagal memuat produk. Cek koneksi server!</div>";
    }
}

window.switchCategory = function(cat, el) {
    // Reset warna biru di semua tombol nav-link dalam category_nav
    document.querySelectorAll("#category_nav .nav-link").forEach(b => b.classList.remove("active"));
    // Kasih warna biru ke tombol yang baru aja diklik
    el.classList.add("active");
    // Render tabel produknya
    renderByKategori(cat);
}

function renderByKategori(cat) {
    const container = document.getElementById("product_container");
    container.innerHTML = "";
    const providers = allGroupedProducts[cat] || {};

    if (Object.keys(providers).length === 0) {
        container.innerHTML = `<div class="alert alert-info">Belum ada produk di kategori ${cat}.</div>`;
        return;
    }

    for (let pName in providers) {
        let rows = providers[pName].map(p => {
            let statusBadge = p.active ? `<span class="badge bg-success">Aktif</span>` : `<span class="badge bg-secondary">Mati</span>`;
            return `
            <tr>
                <td class="small text-muted">${p.sku}</td>
                <td class="fw-bold">${p.name}</td>
                <td class="small">Rp ${p.cost.toLocaleString('id-ID')}</td>
                <td class="text-primary fw-bold">Rp ${p.price.toLocaleString('id-ID')}</td>
                <td class="text-success small">Rp ${p.profit.toLocaleString('id-ID')}</td>
                <td>${statusBadge}</td>
                <td class="text-center">
                    <button class="btn btn-sm btn-outline-primary" onclick="showEditModal('${p.sku}', ${p.cost}, ${p.price})"><i class="bi bi-pencil-square"></i></button>
                    <button class="btn btn-sm ${p.active ? 'btn-outline-warning' : 'btn-outline-success'}" onclick="toggleProduct('${p.sku}')"><i class="bi bi-power"></i></button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteProduct('${p.sku}')"><i class="bi bi-trash"></i></button>
                </td>
            </tr>`;
        }).join("");

        container.innerHTML += `
            <div class="card border-0 shadow-sm mb-4 rounded-4 overflow-hidden">
                <div class="card-header bg-dark text-white fw-bold d-flex justify-content-between align-items-center p-3">
                    <span><i class="bi bi-tag-fill me-2 text-warning"></i> ${pName.toUpperCase()}</span>
                    <span class="badge bg-secondary">${providers[pName].length} Item</span>
                </div>
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light small">
                            <tr><th>SKU</th><th>Item</th><th>Modal</th><th>Jual</th><th>Profit</th><th>Status</th><th class="text-center">Aksi</th></tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            </div>`;
    }
}

// --- FUNGSI CRUD PRODUK ---
window.showAddProductModal = function() {
    document.getElementById("add_provider").value = "";
    document.getElementById("add_name").value = "";
    document.getElementById("add_sku").value = "";
    document.getElementById("add_cost").value = "";
    document.getElementById("add_price").value = "";
    addModal.show();
}

window.saveNewProduct = async function() {
    const data = {
        provider: document.getElementById("add_provider").value,
        name: document.getElementById("add_name").value,
        sku: document.getElementById("add_sku").value,
        cost: parseInt(document.getElementById("add_cost").value),
        price: parseInt(document.getElementById("add_price").value)
    };
    await api("/admin/api/products", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    addModal.hide();
    loadProducts();
}

window.showEditModal = function(sku, cost, price) {
    document.getElementById("edit_id").value = sku;
    document.getElementById("edit_cost").value = cost;
    document.getElementById("edit_price").value = price;
    editModal.show();
}

window.saveEditProduct = async function() {
    const sku = document.getElementById("edit_id").value;
    const data = {
        cost: parseInt(document.getElementById("edit_cost").value),
        price: parseInt(document.getElementById("edit_price").value)
    };
    await api(`/admin/api/products/${sku}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    editModal.hide();
    loadProducts();
}

window.toggleProduct = async function(sku) {
    await api(`/admin/api/products/${sku}/toggle`, { method: "PUT" });
    loadProducts(); 
}

window.deleteProduct = async function(sku) {
    if(confirm(`Hapus permanen SKU: ${sku}?`)) {
        await api(`/admin/api/products/${sku}`, { method: "DELETE" });
        loadProducts();
    }
}



window.logout = function() {
    localStorage.removeItem("admin_token");
    window.location.href="/admin";
}

// --- INIT ---
document.addEventListener("DOMContentLoaded", () => {
    addModal = new bootstrap.Modal(document.getElementById('addProductModal'));
    editModal = new bootstrap.Modal(document.getElementById('editProductModal'));

    const btnSync = document.getElementById('btnSync');
    if (btnSync) {
        btnSync.addEventListener('click', async function() {
            if (!confirm("Tarik data dari Digiflazz?")) return;
            const originalText = this.innerHTML;
            this.disabled = true;
            this.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Loading...';
            try {
                const res = await api('/admin/sync-products', { method: 'POST' });
                alert(res.message);
                loadProducts();
            } catch (err) { alert("Gagal koneksi."); }
            finally { this.disabled = false; this.innerHTML = originalText; }
        });
    }
    
    loadStats();
    loadOrders();
});