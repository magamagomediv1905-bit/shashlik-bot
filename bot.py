#!/usr/bin/env python3
"""Telegram-бот управления меню Шашлык Центр"""
import json, os, re, base64, logging, requests
from typing import Optional
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes,
)

# ── Config ─────────────────────────────────────────────────────────────────
TOKEN        = "8624425261:AAFjxL9XKVep5XYtd-pDwww4RxqcdhWDQZE"
ADMIN_IDS    = [8701112729, 7956675065]
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = "magamagomediv1905-bit/shashlik-centr"
IMAGES_DIR   = "/tmp/shashlik_images"

os.makedirs(IMAGES_DIR, exist_ok=True)

# ── Conversation states ─────────────────────────────────────────────────────
LIST_CAT = 0
ADD_CAT, ADD_NAME, ADD_PRICE, ADD_WEIGHT, ADD_DESC, ADD_IMG = range(1, 7)
EDIT_CAT, EDIT_ITEM, EDIT_FIELD, EDIT_VALUE, EDIT_PHOTO = range(7, 12)
DEL_CAT, DEL_ITEM, DEL_CONFIRM = range(12, 15)
EDITCAT_CAT, EDITCAT_PHOTO = range(15, 17)
ADDCAT_NAME, ADDCAT_PHOTO = range(17, 19)
DELETECAT_SELECT, DELETECAT_CONFIRM = range(19, 21)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)

# ── GitHub API ──────────────────────────────────────────────────────────────
def _gh_headers() -> dict:
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}


def _gh_get(path: str) -> dict:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    r = requests.get(url, headers=_gh_headers(), timeout=15)
    r.raise_for_status()
    return r.json()


def _gh_put(path: str, content_bytes: bytes, msg: str, sha: str) -> None:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    data = {
        "message": msg,
        "content": base64.b64encode(content_bytes).decode(),
        "sha": sha,
    }
    r = requests.put(url, headers=_gh_headers(), json=data, timeout=15)
    r.raise_for_status()


def _gh_create(path: str, content_bytes: bytes, msg: str) -> None:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    data = {
        "message": msg,
        "content": base64.b64encode(content_bytes).decode(),
    }
    r = requests.put(url, headers=_gh_headers(), json=data, timeout=15)
    r.raise_for_status()


# ── Menu I/O ────────────────────────────────────────────────────────────────
def load_menu() -> dict:
    resp = _gh_get("menu.json")
    raw  = base64.b64decode(resp["content"])
    return json.loads(raw)


def save_and_deploy(menu: dict, commit_msg: str) -> None:
    menu_bytes = json.dumps(menu, ensure_ascii=False, indent=2).encode()
    menu_sha   = _gh_get("menu.json")["sha"]
    _gh_put("menu.json", menu_bytes, f"🍖 Bot: {commit_msg}", menu_sha)

    html_resp  = _gh_get("index.html")
    html_bytes = base64.b64decode(html_resp["content"])
    html       = html_bytes.decode("utf-8")
    js_block   = _gen_js(menu)
    new_html   = re.sub(
        r"// ==BEGIN_MENU_DATA==.*?// ==END_MENU_DATA==",
        f"// ==BEGIN_MENU_DATA==\n{js_block}\n// ==END_MENU_DATA==",
        html,
        flags=re.DOTALL,
    )
    _gh_put("index.html", new_html.encode("utf-8"), f"🍖 Bot: {commit_msg}", html_resp["sha"])


def _gen_js(menu: dict) -> str:
    cats  = menu["categories"]
    order = menu["cat_order"]
    lines = ["const MENU_DATA = {"]
    for slug in order:
        cat = cats[slug]
        n = json.dumps(cat["name"], ensure_ascii=False)
        d = json.dumps(cat.get("desc", ""), ensure_ascii=False)
        lines.append(f'  "{slug}": {{name:{n},desc:{d},items:[')
        for item in cat["items"]:
            nm = json.dumps(item["name"],           ensure_ascii=False)
            wt = json.dumps(item.get("weight", ""), ensure_ascii=False)
            dc = json.dumps(item.get("desc", ""),   ensure_ascii=False)
            im = json.dumps(item.get("img", ""),    ensure_ascii=False)
            pr = item["price"]
            lines.append(f"    {{name:{nm},price:{pr},weight:{wt},desc:{dc},img:{im}}},")
        lines.append("  ]},")
    lines.append("};")
    order_js = json.dumps(order, ensure_ascii=False)
    lines.append(f"const CAT_ORDER = {order_js};")
    cat_imgs = {slug: cats[slug].get("img", "") for slug in order}
    lines.append(f"const CAT_IMGS = {json.dumps(cat_imgs, ensure_ascii=False)};")
    return "\n".join(lines)


# ── Photo helpers ────────────────────────────────────────────────────────────
def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name.lower())


async def download_and_upload_photo(update: Update, item_name: str) -> str:
    """Download photo from Telegram, push to GitHub, return relative URL."""
    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    ext   = os.path.splitext(tg_file.file_path)[1] or ".jpg"
    fname = f"{_safe_filename(item_name)}{ext}"
    tmp   = os.path.join(IMAGES_DIR, fname)

    await tg_file.download_to_drive(tmp)
    with open(tmp, "rb") as f:
        img_bytes = f.read()

    gh_path = f"images/{fname}"
    try:
        existing = _gh_get(gh_path)
        _gh_put(gh_path, img_bytes, f"🍖 Bot: photo {item_name}", existing["sha"])
    except requests.HTTPError:
        _gh_create(gh_path, img_bytes, f"🍖 Bot: photo {item_name}")

    return gh_path


# ── Helpers ─────────────────────────────────────────────────────────────────
def is_owner(update: Update) -> bool:
    return update.effective_user.id in ADMIN_IDS


def cat_keyboard(menu: dict) -> ReplyKeyboardMarkup:
    names = [[menu["categories"][s]["name"]] for s in menu["cat_order"]]
    return ReplyKeyboardMarkup(names, resize_keyboard=True, one_time_keyboard=True)


def slug_by_name(menu: dict, name: str) -> Optional[str]:
    for slug, cat in menu["categories"].items():
        if cat["name"] == name:
            return slug
    return None


def name_to_slug(name: str) -> str:
    tr = str.maketrans(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ",
        "abvgdeyozhziyklmnoprstufhtschshh_y_eyuaABVGDEYOZHZIYKLMNOPRSTUFHTSCHSHH_Y_EYUA"
    )
    slug = name.lower().translate(tr)
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or "cat"


async def _expired(update: Update, cmd: str) -> None:
    await update.message.reply_text(
        f"⚠️ Сессия истекла — бот перезапустился. Начни заново: /{cmd}",
        reply_markup=ReplyKeyboardRemove(),
    )


def items_text(cat: dict) -> str:
    lines = [f'📂 *{cat["name"]}*\n']
    for i, item in enumerate(cat["items"], 1):
        w = f" ({item['weight']})" if item.get("weight") else ""
        lines.append(f"{i}. {item['name']}{w} — *{item['price']} ₽*")
    return "\n".join(lines)


# ── /start ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.message.reply_text("У вас нет доступа")
        return
    await update.message.reply_text(
        "🍖 *Шашлык Центр — управление меню*\n\n"
        "/menu — все категории\n"
        "/list — блюда в категории\n"
        "/add — добавить блюдо\n"
        "/edit — изменить блюдо\n"
        "/delete — удалить блюдо\n"
        "/addcat — добавить категорию\n"
        "/deletecat — удалить категорию\n"
        "/editcat — изменить фото категории\n"
        "/cancel — отменить",
        parse_mode="Markdown",
    )


# ── /menu ───────────────────────────────────────────────────────────────────
async def cmd_menu(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.message.reply_text("У вас нет доступа")
        return
    menu = load_menu()
    lines = ["📋 *Категории меню:*\n"]
    for slug in menu["cat_order"]:
        cat = menu["categories"][slug]
        lines.append(f"• {cat['name']} — {len(cat['items'])} блюд")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /list ───────────────────────────────────────────────────────────────────
async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_owner(update):
        await update.message.reply_text("У вас нет доступа")
        return ConversationHandler.END
    menu = load_menu()
    ctx.user_data["menu"] = menu

    if ctx.args:
        slug = slug_by_name(menu, " ".join(ctx.args))
        if slug:
            await update.message.reply_text(
                items_text(menu["categories"][slug]), parse_mode="Markdown"
            )
            return ConversationHandler.END

    await update.message.reply_text("📂 Выберите категорию:", reply_markup=cat_keyboard(menu))
    return LIST_CAT


async def list_select_cat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "list"); return ConversationHandler.END
    menu = ctx.user_data["menu"]
    slug = slug_by_name(menu, update.message.text)
    rm   = ReplyKeyboardRemove()
    if not slug:
        await update.message.reply_text("❌ Не найдена", reply_markup=rm)
        return ConversationHandler.END
    await update.message.reply_text(
        items_text(menu["categories"][slug]), parse_mode="Markdown", reply_markup=rm
    )
    return ConversationHandler.END


# ── /add ────────────────────────────────────────────────────────────────────
async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_owner(update):
        await update.message.reply_text("У вас нет доступа")
        return ConversationHandler.END
    menu = load_menu()
    ctx.user_data.clear()
    ctx.user_data["menu"] = menu
    await update.message.reply_text(
        "➕ *Добавить блюдо*\n\nВыберите категорию:",
        parse_mode="Markdown",
        reply_markup=cat_keyboard(menu),
    )
    return ADD_CAT


async def add_cat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "add"); return ConversationHandler.END
    menu = ctx.user_data["menu"]
    slug = slug_by_name(menu, update.message.text)
    if not slug:
        await update.message.reply_text("❌ Категория не найдена, выберите из списка:")
        return ADD_CAT
    ctx.user_data["slug"] = slug
    await update.message.reply_text(
        f"Категория: *{update.message.text}*\n\nНазвание блюда:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ADD_NAME


async def add_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["item_name"] = update.message.text.strip()
    await update.message.reply_text("Цена (₽), только цифры:")
    return ADD_PRICE


async def add_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        price = int(update.message.text.strip())
        if price <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введите целое число больше 0:")
        return ADD_PRICE
    ctx.user_data["item_price"] = price
    await update.message.reply_text("Вес (например: 200г, 500мл) или /skip:")
    return ADD_WEIGHT


async def add_weight(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()
    ctx.user_data["item_weight"] = "" if t == "/skip" else t
    await update.message.reply_text("Описание или /skip:")
    return ADD_DESC


async def add_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()
    ctx.user_data["item_desc"] = "" if t == "/skip" else t
    await update.message.reply_text(
        "📸 Пришлите фото блюда или напишите /skip чтобы пропустить:"
    )
    return ADD_IMG


async def add_img_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "add"); return ConversationHandler.END
    item_name = ctx.user_data["item_name"]
    await update.message.reply_text("⏳ Загружаю фото на GitHub...")
    img_path = await download_and_upload_photo(update, item_name)
    return await _finish_add(update, ctx, img_path)


async def add_img_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "add"); return ConversationHandler.END
    return await _finish_add(update, ctx, "")


async def _finish_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE, img: str) -> int:
    menu = ctx.user_data["menu"]
    slug = ctx.user_data["slug"]
    new_item = {
        "name":   ctx.user_data["item_name"],
        "price":  ctx.user_data["item_price"],
        "weight": ctx.user_data["item_weight"],
        "desc":   ctx.user_data["item_desc"],
        "img":    img,
    }
    menu["categories"][slug]["items"].append(new_item)
    await update.message.reply_text("⏳ Обновляю сайт через GitHub API...")
    save_and_deploy(menu, f"add {new_item['name']}")
    photo_note = f"\nФото: `{img}`" if img else ""
    await update.message.reply_text(
        f"✅ *{new_item['name']}* добавлено — {new_item['price']} ₽{photo_note}\n"
        "Сайт обновится на GitHub Pages через ~1 мин.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /edit ───────────────────────────────────────────────────────────────────
async def cmd_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_owner(update):
        await update.message.reply_text("У вас нет доступа")
        return ConversationHandler.END
    menu = load_menu()
    ctx.user_data.clear()
    ctx.user_data["menu"] = menu
    await update.message.reply_text(
        "✏️ *Изменить блюдо*\n\nВыберите категорию:",
        parse_mode="Markdown",
        reply_markup=cat_keyboard(menu),
    )
    return EDIT_CAT


async def edit_cat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "edit"); return ConversationHandler.END
    menu = ctx.user_data["menu"]
    slug = slug_by_name(menu, update.message.text)
    if not slug:
        await update.message.reply_text("❌ Не найдена, выберите из списка:")
        return EDIT_CAT
    ctx.user_data["slug"] = slug
    await update.message.reply_text(
        items_text(menu["categories"][slug]) + "\n\nВведите номер блюда:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return EDIT_ITEM


async def edit_item(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "edit"); return ConversationHandler.END
    menu  = ctx.user_data["menu"]
    slug  = ctx.user_data["slug"]
    items = menu["categories"][slug]["items"]
    try:
        idx = int(update.message.text.strip()) - 1
        if not (0 <= idx < len(items)):
            raise ValueError
    except ValueError:
        await update.message.reply_text(f"❌ Введите число от 1 до {len(items)}:")
        return EDIT_ITEM
    ctx.user_data["item_idx"] = idx
    item = items[idx]
    fields = [["Название", "Цена"], ["Вес", "Описание"], ["Изменить фото"]]
    await update.message.reply_text(
        f'Блюдо: *{item["name"]}* ({item["price"]} ₽)\n\nЧто изменить?',
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(fields, resize_keyboard=True, one_time_keyboard=True),
    )
    return EDIT_FIELD


async def edit_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "edit"); return ConversationHandler.END
    text = update.message.text

    if text == "Изменить фото":
        menu  = ctx.user_data["menu"]
        slug  = ctx.user_data["slug"]
        idx   = ctx.user_data["item_idx"]
        item  = menu["categories"][slug]["items"][idx]
        cur   = item.get("img", "") or "нет"
        await update.message.reply_text(
            f"Текущее фото: `{cur}`\n\n📸 Пришлите новое фото блюда:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EDIT_PHOTO

    field_map = {"Название": "name", "Цена": "price", "Вес": "weight", "Описание": "desc"}
    field = field_map.get(text)
    if not field:
        await update.message.reply_text("❌ Выберите поле из кнопок:")
        return EDIT_FIELD
    ctx.user_data["edit_field"] = field
    menu    = ctx.user_data["menu"]
    slug    = ctx.user_data["slug"]
    idx     = ctx.user_data["item_idx"]
    current = menu["categories"][slug]["items"][idx].get(field, "")
    await update.message.reply_text(
        f"Текущее: *{current}*\n\nНовое значение:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return EDIT_VALUE


async def edit_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "edit"); return ConversationHandler.END
    menu  = ctx.user_data["menu"]
    slug  = ctx.user_data["slug"]
    idx   = ctx.user_data["item_idx"]
    field = ctx.user_data["edit_field"]
    value = update.message.text.strip()
    if field == "price":
        try:
            value = int(value)
        except ValueError:
            await update.message.reply_text("❌ Цена должна быть числом:")
            return EDIT_VALUE
    menu["categories"][slug]["items"][idx][field] = value
    name = menu["categories"][slug]["items"][idx]["name"]
    await update.message.reply_text("⏳ Обновляю сайт через GitHub API...")
    save_and_deploy(menu, f"edit {name} → {field}")
    await update.message.reply_text(
        f"✅ *{name}* обновлено!\nСайт обновится на GitHub Pages через ~1 мин.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def edit_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "edit"); return ConversationHandler.END
    menu = ctx.user_data["menu"]
    slug = ctx.user_data["slug"]
    idx  = ctx.user_data["item_idx"]
    item = menu["categories"][slug]["items"][idx]

    await update.message.reply_text("⏳ Загружаю фото на GitHub...")
    img_path  = await download_and_upload_photo(update, item["name"])
    item["img"] = img_path

    await update.message.reply_text("⏳ Обновляю сайт через GitHub API...")
    save_and_deploy(menu, f"photo {item['name']}")
    await update.message.reply_text(
        f"✅ Фото для *{item['name']}* обновлено!\n"
        f"Путь: `{img_path}`\n"
        "Сайт обновится на GitHub Pages через ~1 мин.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /delete ─────────────────────────────────────────────────────────────────
async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_owner(update):
        await update.message.reply_text("У вас нет доступа")
        return ConversationHandler.END
    menu = load_menu()
    ctx.user_data.clear()
    ctx.user_data["menu"] = menu
    await update.message.reply_text(
        "🗑 *Удалить блюдо*\n\nВыберите категорию:",
        parse_mode="Markdown",
        reply_markup=cat_keyboard(menu),
    )
    return DEL_CAT


async def del_cat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "delete"); return ConversationHandler.END
    menu = ctx.user_data["menu"]
    slug = slug_by_name(menu, update.message.text)
    if not slug:
        await update.message.reply_text("❌ Не найдена, выберите из списка:")
        return DEL_CAT
    ctx.user_data["slug"] = slug
    await update.message.reply_text(
        items_text(menu["categories"][slug]) + "\n\nВведите номер блюда:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return DEL_ITEM


async def del_item(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "delete"); return ConversationHandler.END
    menu  = ctx.user_data["menu"]
    slug  = ctx.user_data["slug"]
    items = menu["categories"][slug]["items"]
    try:
        idx = int(update.message.text.strip()) - 1
        if not (0 <= idx < len(items)):
            raise ValueError
    except ValueError:
        await update.message.reply_text(f"❌ Введите число от 1 до {len(items)}:")
        return DEL_ITEM
    ctx.user_data["item_idx"] = idx
    item = items[idx]
    kb   = ReplyKeyboardMarkup(
        [["✅ Да, удалить", "❌ Отмена"]], resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text(
        f'Удалить *{item["name"]}* ({item["price"]} ₽)?',
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return DEL_CONFIRM


async def del_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "delete"); return ConversationHandler.END
    if "Отмена" in update.message.text or "❌" in update.message.text:
        await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    menu = ctx.user_data["menu"]
    slug = ctx.user_data["slug"]
    idx  = ctx.user_data["item_idx"]
    gone = menu["categories"][slug]["items"].pop(idx)
    await update.message.reply_text("⏳ Обновляю сайт через GitHub API...")
    save_and_deploy(menu, f"delete {gone['name']}")
    await update.message.reply_text(
        f"✅ *{gone['name']}* удалено!\nСайт обновится на GitHub Pages через ~1 мин.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ── /editcat ────────────────────────────────────────────────────────────────
async def cmd_editcat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_owner(update):
        await update.message.reply_text("У вас нет доступа")
        return ConversationHandler.END
    menu = load_menu()
    ctx.user_data.clear()
    ctx.user_data["menu"] = menu
    await update.message.reply_text(
        "🖼 *Изменить фото категории*\n\nВыберите категорию:",
        parse_mode="Markdown",
        reply_markup=cat_keyboard(menu),
    )
    return EDITCAT_CAT


async def editcat_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "editcat"); return ConversationHandler.END
    menu = ctx.user_data["menu"]
    slug = slug_by_name(menu, update.message.text)
    if not slug:
        await update.message.reply_text("❌ Не найдена, выберите из списка:")
        return EDITCAT_CAT
    ctx.user_data["slug"] = slug
    cur = menu["categories"][slug].get("img", "") or "нет"
    await update.message.reply_text(
        f"Категория: *{update.message.text}*\n"
        f"Текущее фото: `{cur[:60]}{'...' if len(cur)>60 else ''}`\n\n"
        "📸 Пришлите новое фото категории:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return EDITCAT_PHOTO


async def editcat_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "editcat"); return ConversationHandler.END
    menu = ctx.user_data["menu"]
    slug = ctx.user_data["slug"]
    cat  = menu["categories"][slug]

    await update.message.reply_text("⏳ Загружаю фото на GitHub...")
    img_path = await download_and_upload_photo(update, f"cat_{slug}")
    cat["img"] = img_path

    await update.message.reply_text("⏳ Обновляю сайт через GitHub API...")
    save_and_deploy(menu, f"cat photo {cat['name']}")
    await update.message.reply_text(
        f"✅ Фото категории *{cat['name']}* обновлено!\n"
        f"Путь: `{img_path}`\n"
        "Сайт обновится на GitHub Pages через ~1 мин.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /addcat ─────────────────────────────────────────────────────────────────
async def cmd_addcat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_owner(update):
        await update.message.reply_text("У вас нет доступа")
        return ConversationHandler.END
    ctx.user_data.clear()
    await update.message.reply_text(
        "➕ *Добавить категорию*\n\nВведите название новой категории:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ADDCAT_NAME


async def addcat_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    slug = name_to_slug(name)
    menu = load_menu()
    if slug in menu["categories"]:
        await update.message.reply_text(
            f"❌ Категория с таким именем уже существует (slug: `{slug}`).\nВведите другое название:",
            parse_mode="Markdown",
        )
        return ADDCAT_NAME
    ctx.user_data["menu"]     = menu
    ctx.user_data["cat_name"] = name
    ctx.user_data["cat_slug"] = slug
    await update.message.reply_text(
        f"Название: *{name}*\nSlug: `{slug}`\n\n"
        "📸 Пришлите фото категории или напишите /skip:",
        parse_mode="Markdown",
    )
    return ADDCAT_PHOTO


async def addcat_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "addcat"); return ConversationHandler.END
    return await _finish_addcat(update, ctx, "")


async def addcat_photo_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "addcat"); return ConversationHandler.END
    await update.message.reply_text("⏳ Загружаю фото на GitHub...")
    img = await download_and_upload_photo(update, f"cat_{ctx.user_data['cat_slug']}")
    return await _finish_addcat(update, ctx, img)


async def _finish_addcat(update: Update, ctx: ContextTypes.DEFAULT_TYPE, img: str) -> int:
    menu = ctx.user_data["menu"]
    name = ctx.user_data["cat_name"]
    slug = ctx.user_data["cat_slug"]
    menu["cat_order"].append(slug)
    menu["categories"][slug] = {"name": name, "img": img, "items": []}
    await update.message.reply_text("⏳ Обновляю сайт через GitHub API...")
    save_and_deploy(menu, f"add category {name}")
    await update.message.reply_text(
        f"✅ Категория *{name}* добавлена!\n"
        f"Slug: `{slug}`\n"
        "Теперь добавляйте блюда через /add.\n"
        "Сайт обновится через ~1 мин.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /deletecat ───────────────────────────────────────────────────────────────
async def cmd_deletecat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_owner(update):
        await update.message.reply_text("У вас нет доступа")
        return ConversationHandler.END
    menu = load_menu()
    ctx.user_data.clear()
    ctx.user_data["menu"] = menu
    await update.message.reply_text(
        "🗑 *Удалить категорию*\n\nВыберите категорию:",
        parse_mode="Markdown",
        reply_markup=cat_keyboard(menu),
    )
    return DELETECAT_SELECT


async def deletecat_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "deletecat"); return ConversationHandler.END
    menu = ctx.user_data["menu"]
    slug = slug_by_name(menu, update.message.text)
    if not slug:
        await update.message.reply_text("❌ Не найдена, выберите из списка:")
        return DELETECAT_SELECT
    ctx.user_data["slug"] = slug
    count = len(menu["categories"][slug]["items"])
    kb = ReplyKeyboardMarkup(
        [["✅ Да, удалить", "❌ Отмена"]], resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text(
        f"Удалить категорию *{update.message.text}*?\n"
        f"Вместе с ней будет удалено *{count} блюд*.",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return DELETECAT_CONFIRM


async def deletecat_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if "menu" not in ctx.user_data:
        await _expired(update, "deletecat"); return ConversationHandler.END
    if "Отмена" in update.message.text or "❌" in update.message.text:
        await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    menu = ctx.user_data["menu"]
    slug = ctx.user_data["slug"]
    name = menu["categories"][slug]["name"]
    menu["cat_order"].remove(slug)
    del menu["categories"][slug]
    await update.message.reply_text("⏳ Обновляю сайт через GitHub API...")
    save_and_deploy(menu, f"delete category {name}")
    await update.message.reply_text(
        f"✅ Категория *{name}* удалена!\nСайт обновится через ~1 мин.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ── /cancel ─────────────────────────────────────────────────────────────────
async def cmd_cancel(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    if not GITHUB_TOKEN:
        logging.warning("⚠️  GITHUB_TOKEN не задан — изменения меню не будут сохраняться!")

    from telegram.ext import ApplicationBuilder
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu",  cmd_menu))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("list", cmd_list)],
        states={LIST_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, list_select_cat)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", cmd_add)],
        states={
            ADD_CAT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_cat)],
            ADD_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ADD_PRICE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_price)],
            ADD_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_weight)],
            ADD_DESC:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_desc)],
            ADD_IMG: [
                MessageHandler(filters.PHOTO, add_img_photo),
                MessageHandler(filters.Regex(r"^/skip$"), add_img_skip),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("edit", cmd_edit)],
        states={
            EDIT_CAT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_cat)],
            EDIT_ITEM:  [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_item)],
            EDIT_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field)],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value)],
            EDIT_PHOTO: [MessageHandler(filters.PHOTO, edit_photo)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("delete", cmd_delete)],
        states={
            DEL_CAT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, del_cat)],
            DEL_ITEM:    [MessageHandler(filters.TEXT & ~filters.COMMAND, del_item)],
            DEL_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, del_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("editcat", cmd_editcat)],
        states={
            EDITCAT_CAT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, editcat_select)],
            EDITCAT_PHOTO: [MessageHandler(filters.PHOTO, editcat_photo)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("addcat", cmd_addcat)],
        states={
            ADDCAT_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, addcat_name)],
            ADDCAT_PHOTO: [
                MessageHandler(filters.PHOTO, addcat_photo_upload),
                MessageHandler(filters.Regex(r"^/skip$"), addcat_photo),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("deletecat", cmd_deletecat)],
        states={
            DELETECAT_SELECT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, deletecat_select)],
            DELETECAT_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, deletecat_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))

    logging.info("🍖 Бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
