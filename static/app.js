const state = {
  csrfToken: "",
  user: null,
  restaurantOpen: true,
  setupAllowed: false,
  categories: [],
  dishes: [],
  slots: [],
  activeCategory: null,
  search: "",
  cart: loadCart(),
  toastTimer: null
};

const money = (cents) => new Intl.NumberFormat("uk-UA", {
  style: "currency",
  currency: "UAH",
  maximumFractionDigits: 0
}).format(cents / 100);

function loadCart() {
  try {
    const saved = JSON.parse(localStorage.getItem("smachno-cart") || "[]");
    return Array.isArray(saved) ? saved.filter((item) =>
      Number.isInteger(item.dishId) &&
      Number.isInteger(item.quantity) &&
      Array.isArray(item.modifierIds)
    ) : [];
  } catch {
    return [];
  }
}

function saveCart() {
  localStorage.setItem("smachno-cart", JSON.stringify(state.cart));
  renderCart();
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET" && state.csrfToken) {
    headers.set("X-CSRF-Token", state.csrfToken);
  }
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
    body: options.body === undefined ? undefined : JSON.stringify(options.body)
  });
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error("Сервер повернув неочікувану відповідь.");
  }
  if (!response.ok) throw new Error(result.error || "Не вдалося виконати запит.");
  return result;
}

function showToast(message, isError = false) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.toggle("is-error", isError);
  toast.classList.add("is-visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 3600);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function dishEmoji(dish) {
  if (dish.name.includes("Борщ")) return "🍲";
  if (dish.name.includes("Вареники")) return "🥟";
  if (dish.name.includes("котлета")) return "🍽️";
  if (dish.name.includes("суп")) return "🥣";
  if (dish.name.includes("Салат")) return "🥗";
  if (dish.name.includes("Узвар")) return "🍹";
  return "🍴";
}

function renderCategories() {
  const list = document.querySelector("#category-list");
  list.replaceChildren();
  const allButton = element("button", `category-button${state.activeCategory === null ? " is-active" : ""}`, "Усі страви");
  allButton.type = "button";
  allButton.addEventListener("click", () => {
    state.activeCategory = null;
    renderCategories();
    renderMenu();
  });
  list.append(allButton);
  for (const category of state.categories) {
    const button = element("button", `category-button${state.activeCategory === category.id ? " is-active" : ""}`, category.name);
    button.type = "button";
    button.addEventListener("click", () => {
      state.activeCategory = category.id;
      renderCategories();
      renderMenu();
    });
    list.append(button);
  }
}

function renderMenu() {
  const menu = document.querySelector("#menu-list");
  menu.replaceChildren();
  const filtered = state.dishes.filter((dish) => {
    const matchesCategory = state.activeCategory === null || dish.category_id === state.activeCategory;
    const query = state.search.toLocaleLowerCase("uk");
    const matchesSearch = !query || [dish.name, dish.description, dish.ingredients]
      .some((value) => value.toLocaleLowerCase("uk").includes(query));
    return matchesCategory && matchesSearch;
  });
  if (!filtered.length) {
    menu.append(element("p", "no-results", "За вашим запитом страв не знайдено."));
    return;
  }
  for (const dish of filtered) menu.append(createDishCard(dish));
}

function createDishCard(dish) {
  const card = element("article", "dish-card");
  const art = element("div", "dish-art");
  art.setAttribute("aria-hidden", "true");
  art.append(element("span", "", dishEmoji(dish)));
  const info = element("div", "dish-info");
  info.append(element("p", "dish-category", dish.category_name));
  info.append(element("h3", "", dish.name));
  info.append(element("p", "dish-description", dish.description));
  info.append(element("p", "dish-meta", `Склад: ${dish.ingredients}. Алергени: ${dish.allergens}.`));

  if (dish.modifiers.length) {
    const modifierList = element("div", "modifier-list");
    modifierList.setAttribute("aria-label", `Модифікатори до страви «${dish.name}»`);
    for (const modifier of dish.modifiers) {
      const label = element("label", "modifier-option");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = String(modifier.id);
      checkbox.dataset.modifierDish = String(dish.id);
      checkbox.addEventListener("change", () => {
        const selected = card.querySelectorAll("input[type=checkbox]:checked");
        if (selected.length > 3) {
          checkbox.checked = false;
          showToast("Для однієї страви можна обрати не більше трьох модифікаторів.", true);
        }
      });
      label.append(checkbox, document.createTextNode(modifier.name));
      label.append(element("span", "modifier-price", `+ ${money(modifier.price_cents)}`));
      modifierList.append(label);
    }
    info.append(modifierList);
  }

  const buyRow = element("div", "dish-buy-row");
  buyRow.append(element("span", "dish-price", money(dish.price_cents)));
  if (!dish.is_available) {
    buyRow.append(element("span", "unavailable", "Тимчасово немає"));
  } else {
    const add = element("button", "add-button", "+");
    add.type = "button";
    add.setAttribute("aria-label", `Додати «${dish.name}» до кошика`);
    add.disabled = !state.restaurantOpen;
    add.addEventListener("click", () => {
      const modifierIds = [...card.querySelectorAll("input[type=checkbox]:checked")]
        .map((input) => Number(input.value))
        .sort((a, b) => a - b);
      addToCart(dish.id, modifierIds);
    });
    buyRow.append(add);
  }
  info.append(buyRow);
  card.append(art, info);
  return card;
}

function cartKey(dishId, modifierIds) {
  return `${dishId}:${modifierIds.join(",")}`;
}

function addToCart(dishId, modifierIds) {
  const key = cartKey(dishId, modifierIds);
  const existing = state.cart.find((item) => cartKey(item.dishId, item.modifierIds) === key);
  if (existing) existing.quantity = Math.min(20, existing.quantity + 1);
  else state.cart.push({ dishId, modifierIds, quantity: 1 });
  saveCart();
  showToast("Страву додано до кошика.");
}

function selectedDish(cartItem) {
  const dish = state.dishes.find((item) => item.id === cartItem.dishId);
  if (!dish) return null;
  const modifiers = cartItem.modifierIds
    .map((id) => dish.modifiers.find((modifier) => modifier.id === id))
    .filter(Boolean);
  const unitTotal = dish.price_cents + modifiers.reduce((sum, item) => sum + item.price_cents, 0);
  return { dish, modifiers, unitTotal };
}

function renderCart() {
  const container = document.querySelector("#basket-items");
  container.replaceChildren();
  let itemCount = 0;
  let total = 0;
  state.cart = state.cart.filter((item) => selectedDish(item));
  state.cart.forEach((item, index) => {
    const selected = selectedDish(item);
    if (!selected) return;
    const { dish, modifiers, unitTotal } = selected;
    itemCount += item.quantity;
    total += unitTotal * item.quantity;
    const row = element("div", "basket-item");
    const heading = element("span", "basket-item-name", dish.name);
    const price = element("span", "basket-item-name", money(unitTotal * item.quantity));
    const details = element("span", "basket-item-options",
      modifiers.length ? modifiers.map((modifier) => modifier.name).join(", ") : "Без додатків");
    const controls = element("div", "basket-item-controls");
    const decrement = element("button", "quantity-button", "−");
    decrement.type = "button";
    decrement.setAttribute("aria-label", `Зменшити кількість: ${dish.name}`);
    decrement.addEventListener("click", () => changeQuantity(index, -1));
    const quantity = element("span", "", String(item.quantity));
    const increment = element("button", "quantity-button", "+");
    increment.type = "button";
    increment.setAttribute("aria-label", `Збільшити кількість: ${dish.name}`);
    increment.addEventListener("click", () => changeQuantity(index, 1));
    const remove = element("button", "remove-button", "Видалити");
    remove.type = "button";
    remove.addEventListener("click", () => {
      state.cart.splice(index, 1);
      saveCart();
    });
    controls.append(decrement, quantity, increment, remove);
    row.append(heading, price, details, controls);
    container.append(row);
  });
  document.querySelector("#basket-empty").hidden = state.cart.length > 0;
  document.querySelector("#basket-count").textContent = String(itemCount);
  document.querySelector("#header-cart-count").textContent = String(itemCount);
  document.querySelector("#basket-total").textContent = money(total);
  document.querySelector("#checkout-button").disabled =
    itemCount === 0 || !state.restaurantOpen || !document.querySelector("#pickup-slot").value;
}

function changeQuantity(index, delta) {
  const item = state.cart[index];
  if (!item) return;
  item.quantity += delta;
  if (item.quantity <= 0) state.cart.splice(index, 1);
  if (item.quantity > 20) item.quantity = 20;
  saveCart();
}

async function loadMenu() {
  const data = await api("/api/menu");
  state.categories = data.categories;
  state.dishes = data.dishes;
  renderCategories();
  renderMenu();
  renderCart();
  if (state.user?.role === "operator") renderOperatorMenu();
}

async function loadSlots() {
  const data = await api("/api/slots");
  state.slots = data.slots;
  const select = document.querySelector("#pickup-slot");
  const previous = select.value;
  select.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = state.slots.length ? "Оберіть час отримання" : "Наразі немає доступних інтервалів";
  select.append(placeholder);
  for (const slot of state.slots) {
    const option = document.createElement("option");
    option.value = String(slot.id);
    const date = new Date(slot.starts_at);
    option.textContent = `${new Intl.DateTimeFormat("uk-UA", {
      weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"
    }).format(date)} · залишилось ${slot.remaining}`;
    select.append(option);
  }
  if (state.slots.some((slot) => String(slot.id) === previous)) select.value = previous;
  renderCart();
}

function showAuthMode(mode) {
  document.querySelector("#login-form").hidden = mode !== "login";
  document.querySelector("#register-form").hidden = mode !== "register";
  document.querySelector("#operator-setup-form").hidden = mode !== "setup";
  for (const tab of document.querySelectorAll(".auth-tab")) {
    tab.classList.toggle("is-active", tab.dataset.authMode === mode);
  }
}

function renderUser() {
  const signedIn = Boolean(state.user);
  document.querySelector("#auth-forms").hidden = signedIn;
  document.querySelector("#signed-in-panel").hidden = !signedIn;
  document.querySelector("#orders-link").hidden = !signedIn || state.user.role !== "customer";
  document.querySelector("#account-link").textContent = signedIn ? state.user.name : "Увійти";
  document.querySelector("#account-title").textContent = signedIn
    ? `Вітаємо, ${state.user.name}!`
    : "Раді вас бачити";
  document.querySelector("#account-description").textContent = signedIn
    ? "Ваш обліковий запис готовий. Тут можна вийти з акаунта або повернутися до замовлень."
    : "Увійдіть або створіть акаунт, щоб оформити замовлення й стежити за його станом.";
  document.querySelector("#signed-in-name").textContent = state.user
    ? `${state.user.name} · ${state.user.email}` : "";
  document.querySelector("#signed-in-role").textContent = state.user?.role === "operator"
    ? "Оператор закладу" : "Клієнт";
  document.querySelector("#orders").hidden = !signedIn || state.user.role !== "customer";
  document.querySelector("#operator-panel").hidden = !signedIn || state.user.role !== "operator";
  if (signedIn && state.user.role === "operator") {
    loadOperatorDashboard().catch((error) => showToast(error.message, true));
  } else if (signedIn && state.user.role === "customer") {
    loadOrders().catch((error) => showToast(error.message, true));
  }
  renderCart();
}

async function loadOrders() {
  if (!state.user) return;
  const { orders } = await api("/api/orders");
  renderOrders(document.querySelector("#orders-list"), orders, false);
  if (state.user.role === "operator") {
    renderOrders(document.querySelector("#operator-orders"), orders, true);
  }
}

function renderOrders(container, orders, operatorView) {
  container.replaceChildren();
  if (!orders.length) {
    container.append(element("p", "empty-orders", "Замовлень поки немає."));
    return;
  }
  for (const order of orders) {
    const card = element("article", "order-card");
    const top = element("div", "order-topline");
    const title = element("h3", "order-title", `Замовлення №${order.id}`);
    const badge = element("span", "order-status", order.status_label);
    top.append(title, badge);
    const details = element("p", "order-details",
      `Отримання: ${formatDate(order.pickup_at)} · Створено: ${formatDate(order.created_at)}${operatorView ? ` · ${order.customer_name}, ${order.customer_phone}` : ""}`);
    const items = element("ul", "order-items");
    for (const item of order.items) {
      const modifierText = item.modifiers.length
        ? ` (${item.modifiers.map((modifier) => modifier.name).join(", ")})`
        : "";
      items.append(element("li", "", `${item.dish_name}${modifierText} × ${item.quantity} — ${money((item.unit_price_cents + item.modifiers.reduce((sum, modifier) => sum + modifier.price_cents, 0)) * item.quantity)}`));
    }
    const total = element("p", "order-details order-total", `Разом: ${money(order.total_cents)}${order.note ? ` · Коментар: ${order.note}` : ""}`);
    card.append(top, details, items, total);
    const actions = element("div", "order-actions");
    const possibleActions = operatorView
      ? {
          new: ["preparing", "cancelled"],
          preparing: ["ready", "cancelled"],
          ready: ["completed"],
          completed: [],
          cancelled: []
        }[order.status]
      : order.status === "new" ? ["cancelled"] : [];
    for (const status of possibleActions) {
      const button = element("button", "", actionLabel(status));
      button.type = "button";
      button.addEventListener("click", () => updateOrder(order.id, status));
      actions.append(button);
    }
    if (actions.childElementCount) card.append(actions);
    container.append(card);
  }
}

function actionLabel(status) {
  return {
    preparing: "Почати готувати",
    ready: "Позначити готовим",
    completed: "Завершити",
    cancelled: "Скасувати замовлення"
  }[status] || status;
}

function formatDate(value) {
  return new Intl.DateTimeFormat("uk-UA", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"
  }).format(new Date(value));
}

async function updateOrder(id, status) {
  try {
    await api(`/api/orders/${id}/status`, { method: "PATCH", body: { status } });
    await loadOrders();
    showToast("Стан замовлення оновлено.");
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderOperatorMenu() {
  const container = document.querySelector("#operator-menu");
  container.replaceChildren();
  for (const dish of state.dishes) {
    const row = element("div", "operator-dish");
    row.append(element("span", "", `${dish.name} · ${money(dish.price_cents)}`));
    const button = element("button", "", dish.is_available ? "Зробити недоступною" : "Зробити доступною");
    button.type = "button";
    button.addEventListener("click", async () => {
      try {
        await api(`/api/operator/dishes/${dish.id}`, {
          method: "PATCH", body: { isAvailable: !dish.is_available }
        });
        await loadMenu();
        showToast("Доступність страви змінено.");
      } catch (error) {
        showToast(error.message, true);
      }
    });
    row.append(button);
    container.append(row);
  }
}

async function loadOperatorDashboard() {
  await loadOrders();
  await loadMenu();
  const button = document.querySelector("#restaurant-toggle");
  button.textContent = state.restaurantOpen
    ? "Приймання замовлень увімкнено"
    : "Заклад зачинений для замовлень";
}

async function setRestaurantOpen(acceptingOrders) {
  const result = await api("/api/operator/restaurant", {
    method: "PATCH", body: { acceptingOrders }
  });
  state.restaurantOpen = result.restaurantOpen;
  document.querySelector("#restaurant-notice").hidden = state.restaurantOpen;
  document.querySelector("#restaurant-toggle").textContent = state.restaurantOpen
    ? "Приймання замовлень увімкнено"
    : "Заклад зачинений для замовлень";
  renderMenu();
  renderCart();
}

async function submitOrder() {
  if (!state.user) {
    showToast("Спочатку увійдіть або створіть акаунт, щоб оформити замовлення.", true);
    document.querySelector("#account").scrollIntoView({ behavior: "smooth" });
    return;
  }
  const slotId = Number(document.querySelector("#pickup-slot").value);
  if (!slotId) {
    showToast("Оберіть інтервал отримання.", true);
    return;
  }
  const button = document.querySelector("#checkout-button");
  button.disabled = true;
  try {
    const result = await api("/api/orders", {
      method: "POST",
      body: {
        slotId,
        note: document.querySelector("#order-note").value,
        idempotencyKey: crypto.randomUUID(),
        items: state.cart.map((item) => ({
          dishId: item.dishId,
          quantity: item.quantity,
          modifierIds: item.modifierIds
        }))
      }
    });
    state.cart = [];
    saveCart();
    document.querySelector("#order-note").value = "";
    await Promise.all([loadOrders(), loadSlots()]);
    document.querySelector("#orders").scrollIntoView({ behavior: "smooth" });
    showToast(`Замовлення №${result.order.id} прийнято.`);
  } catch (error) {
    showToast(error.message, true);
    if (error.message.includes("інтервал") || error.message.includes("місць")) {
      await loadSlots().catch(() => {});
    }
  } finally {
    renderCart();
  }
}

async function authSubmit(event, endpoint) {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  const body = Object.fromEntries(formData.entries());
  try {
    const result = await api(endpoint, { method: "POST", body });
    state.user = result.user;
    state.csrfToken = result.csrfToken;
    renderUser();
    showToast(state.user.role === "operator" ? "Ви увійшли як оператор." : "Вхід виконано.");
    if (state.user.role === "operator") {
      await loadOperatorDashboard();
    }
  } catch (error) {
    showToast(error.message, true);
  }
}

async function start() {
  try {
    const session = await api("/api/session");
    state.user = session.user;
    state.csrfToken = session.csrfToken;
    state.restaurantOpen = session.restaurantOpen;
    state.setupAllowed = session.operatorSetupAllowed;
    document.querySelector("#restaurant-notice").hidden = state.restaurantOpen;
    if (state.setupAllowed) {
      const setupTab = element("button", "auth-tab", "Створити оператора");
      setupTab.type = "button";
      setupTab.dataset.authMode = "setup";
      document.querySelector(".auth-tabs").append(setupTab);
      setupTab.addEventListener("click", () => showAuthMode("setup"));
    }
    renderUser();
    await Promise.all([loadMenu(), loadSlots()]);
  } catch (error) {
    showToast(error.message, true);
  }
}

document.querySelector("#menu-search").addEventListener("input", (event) => {
  state.search = event.target.value.trim();
  renderMenu();
});
document.querySelector("#pickup-slot").addEventListener("change", renderCart);
document.querySelector("#checkout-button").addEventListener("click", submitOrder);
document.querySelector("#login-form").addEventListener("submit", (event) => authSubmit(event, "/api/login"));
document.querySelector("#register-form").addEventListener("submit", (event) => authSubmit(event, "/api/register"));
document.querySelector("#operator-setup-form").addEventListener("submit", (event) => authSubmit(event, "/api/setup/operator"));
document.querySelector("#logout-button").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST", body: {} });
    state.user = null;
    const session = await api("/api/session");
    state.csrfToken = session.csrfToken;
    state.setupAllowed = session.operatorSetupAllowed;
    renderUser();
    showToast("Ви вийшли з акаунта.");
  } catch (error) {
    showToast(error.message, true);
  }
});
document.querySelector("#restaurant-toggle").addEventListener("click", async () => {
  try {
    await setRestaurantOpen(!state.restaurantOpen);
    showToast(state.restaurantOpen ? "Заклад знову приймає замовлення." : "Приймання замовлень призупинено.");
  } catch (error) {
    showToast(error.message, true);
  }
});
document.querySelector('[data-action="refresh-orders"]').addEventListener("click", () => {
  loadOrders().then(() => showToast("Список замовлень оновлено."))
    .catch((error) => showToast(error.message, true));
});
document.querySelectorAll(".auth-tab[data-auth-mode]").forEach((tab) => {
  tab.addEventListener("click", () => showAuthMode(tab.dataset.authMode));
});

start();
setInterval(() => {
  if (!state.user) return;
  loadOrders().catch((error) => showToast(error.message, true));
}, 15000);
