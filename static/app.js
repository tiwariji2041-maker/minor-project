const form = document.querySelector("#compare-form");
const input = document.querySelector("#product-url");
const button = document.querySelector("#compare-btn");
const statusBox = document.querySelector("#status");
const resultsSection = document.querySelector("#results");
const productCard = document.querySelector("#product-card");
const offers = document.querySelector("#offers");
const warning = document.querySelector("#warning");
const sourceNote = document.querySelector("#source-note");
const resultCount = document.querySelector("#result-count");

function money(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(value);
}
function safeExternalLink(url) {
  try {
    const parsed = new URL(url);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "#";
  } catch { return "#"; }
}
function showStatus(message, isError = false) {
  statusBox.textContent = message;
  statusBox.className = "status" + (isError ? " error" : "");
}
function renderProduct(product) {
  productCard.replaceChildren();
  if (product.image) {
    const img = document.createElement("img");
    img.className = "product-image";
    img.src = product.image;
    img.alt = product.title || "Product image";
    img.referrerPolicy = "no-referrer";
    img.onerror = () => { img.replaceWith(makePlaceholder()); };
    productCard.append(img);
  } else productCard.append(makePlaceholder());

  const meta = document.createElement("div");
  meta.className = "product-meta";
  const heading = document.createElement("h2");
  heading.textContent = product.title || "Product details";
  const asin = document.createElement("p");
  asin.textContent = product.asin ? `Amazon ASIN: ${product.asin}` : "Amazon India listing";
  const link = document.createElement("a");
  link.className = "amazon-link";
  link.href = safeExternalLink(product.url);
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "View on Amazon ↗";
  meta.append(heading, asin, link);
  productCard.append(meta);
}
function makePlaceholder() {
  const el = document.createElement("div");
  el.className = "product-placeholder";
  el.textContent = "▧";
  return el;
}
function renderOffers(items, cheapest) {
  offers.replaceChildren();
  const numericPrices = items.filter(x => typeof x.price === "number");
  resultCount.textContent = `${items.length} listings`;
  items.forEach((item) => {
    const row = document.createElement("article");
    row.className = "offer";
    const store = document.createElement("div");
    store.className = "store";
    const logo = document.createElement("div");
    logo.className = "store-logo";
    if (item.image) {
      const img = document.createElement("img");
      img.src = item.image;
      img.alt = "";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.onerror = () => { logo.textContent = (item.store || "S").slice(0,1).toUpperCase(); };
      logo.append(img);
    } else logo.textContent = (item.store || "S").slice(0,1).toUpperCase();

    const details = document.createElement("div");
    const name = document.createElement("div");
    name.className = "store-name";
    name.textContent = item.store || "Online store";
    const title = document.createElement("div");
    title.className = "offer-title";
    title.title = item.title || "";
    title.textContent = item.title || "Product listing";
    details.append(name, title);
    store.append(logo, details);

    const priceWrap = document.createElement("div");
    const price = document.createElement("div");
    price.className = "price";
    price.textContent = money(item.price) || item.price_text || "Check price";
    priceWrap.append(price);
    if (cheapest && item.link === cheapest.link && item.price === cheapest.price) {
      const badge = document.createElement("span");
      badge.className = "best";
      badge.textContent = "LOWEST FOUND";
      priceWrap.append(badge);
    }

    const link = document.createElement("a");
    link.className = "visit";
    link.href = safeExternalLink(item.link);
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "View offer ↗";
    row.append(store, priceWrap, link);
    offers.append(row);
  });
}
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = input.value.trim();
  resultsSection.classList.add("hidden");
  statusBox.classList.remove("hidden");
  showStatus("Searching product listings and comparing prices…");
  button.disabled = true;
  button.textContent = "Searching…";
  try {
    const response = await fetch("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not compare this product.");
    renderProduct(data.product);
    renderOffers(data.results || [], data.cheapest);
    if (data.warning) {
      warning.textContent = data.warning;
      warning.classList.remove("hidden");
    } else warning.classList.add("hidden");
    sourceNote.textContent = data.source_note || "";
    resultsSection.classList.remove("hidden");
    statusBox.classList.add("hidden");
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    showStatus(err.message || "Something went wrong. Please try again.", true);
  } finally {
    button.disabled = false;
    button.innerHTML = 'Compare prices <span>→</span>';
  }
});
