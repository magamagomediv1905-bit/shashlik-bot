#!/usr/bin/env python3
"""Telegram-бот управления меню Шашлык Центр"""
import json, os, re, subprocess, logging
from typing import Optional
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes,
)

# ── Config ─────────────────────────────────────────────────────────────────
TOKEN      = "8624425261:AAE-mQuwzQU2lOC_81ScsuK_Ve2hwnwFk6o"
OWNER_ID   = 8701112729
REPO_DIR   = os.path.dirname(os.path.abspath(__file__))
MENU_JSON  = os.path.join(REPO_DIR, "menu.json")
HTML_FILE  = os.path.join(REPO_DIR, "index.html")
IMAGES_DIR = os.path.join(REPO_DIR, "images")

os.makedirs(IMAGES_DIR, exist_ok=True)

# ── Conversation states ─────────────────────────────────────────────────────
LIST_CAT = 0
ADD_CAT, ADD_NAME, ADD_PRICE, ADD_WEIGHT, ADD_DESC, ADD_IMG = range(1, 7)
EDIT_CAT, EDIT_ITEM, EDIT_FIELD, EDIT_VALUE, EDIT_PHOTO = range(7, 12)
DEL_CAT, DEL_ITEM, DEL_CONFIRM = range(12, 15)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)

# ── Menu I/O ────────────────────────────────────────────────────────────────
def load_menu() -> dict:
    with open(MENU_JSON, encoding="utf-8") as f:
        return json.load(f)


def save_and_deploy(menu: dict, commit_msg: str) -> None:
    with open(MENU_JSON, "w", encoding="utf-8") as f:
        json.dump(menu, f, ensure_ascii=False, indent=2)
    _update_html(menu)
    _git_push(commit_msg)


def _update_html(menu: dict) -> None:
    with open(HTML_FILE, encoding="utf-8") as f:
        html = f.read()
    js_block = _gen_js(menu)
    new_html = re.sub(
        r"// ==BEGIN_MENU_DATA==.*?// ==END_MENU_DATA==",
        f"// ==BEGIN_MENU_DATA==\n{js_block}\n// ==END_MENU_DATA==",
        html,
        flags=re.DOTALL,
    )
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)


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
    return "\n".join(lines)


def _git_push(msg: str) -> None:
    subprocess.run(["git", "add", "menu.json", "index.html", "images/"], cwd=REPO_DIR, check=False)
    result = subprocess.run(
        ["git", "commit", "-m", f"🍖 Bot: {msg}"],
        cwd=REPO_DIR, capture_output=True, text=True
    )
    if "nothing to commit" not in result.stdout:
        subprocess.run(["git", "push"], cwd=REPO_DIR, check=False)


# ── Photo helpers ────────────────────────────────────────────────────────────
def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name.lower())


async def download_photo(update: Update, item_name: str) -> str:
    """Download highest-res photo, save to images/, return relative URL path."""
    photo = update.message.photo[-1]
    file = await photo.get_file()
    ext = os.path.splitext(file.file_path)[1] or ".jpg"
    fname = f"{_safe_filename(item_name)}{ext}"
    dest  = os.path.join(IMAGES_DIR, fname)
    await file.download_to_drive(dest)
    return f"images/{fname}"


# ── Helpers ─────────────────────────────────────────────────────────────────
def is_owner(update: Update) -> bool:
    return update.effective_user.id == OWNER_ID


def cat_keyboard(menu: dict) -> ReplyKeyboardMarkup:
    names = [[menu["categories"][s]["name"]] for s in menu["cat_order"]]
    return ReplyKeyboardMarkup(names, resize_keyboard=True, one_time_keyboard=True)


def slug_by_name(menu: dict, name: str) -> Optional[str]:
    for slug, cat in menu["categories"].items():
        if cat["name"] == name:
            return slug
    return None


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
    """Handle photo upload during /add."""
    item_name = ctx.user_data["item_name"]
    img_path  = await download_photo(update, item_name)
    return await _finish_add(update, ctx, img_path)


async def add_img_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /skip during photo step of /add."""
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
    await update.message.reply_text("⏳ Сохраняю и пушу на GitHub...")
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
    await update.message.reply_text("⏳ Сохраняю и пушу на GitHub...")
    save_and_deploy(menu, f"edit {name} → {field}")
    await update.message.reply_text(
        f"✅ *{name}* обновлено!\nСайт обновится на GitHub Pages через ~1 мин.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def edit_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle new photo upload during /edit."""
    menu = ctx.user_data["menu"]
    slug = ctx.user_data["slug"]
    idx  = ctx.user_data["item_idx"]
    item = menu["categories"][slug]["items"][idx]

    await update.message.reply_text("⏳ Скачиваю фото...")
    img_path = await download_photo(update, item["name"])
    item["img"] = img_path

    await update.message.reply_text("⏳ Сохраняю и пушу на GitHub...")
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
    if "Отмена" in update.message.text or "❌" in update.message.text:
        await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    menu = ctx.user_data["menu"]
    slug = ctx.user_data["slug"]
    idx  = ctx.user_data["item_idx"]
    gone = menu["categories"][slug]["items"].pop(idx)
    await update.message.reply_text("⏳ Сохраняю и пушу на GitHub...")
    save_and_deploy(menu, f"delete {gone['name']}")
    await update.message.reply_text(
        f"✅ *{gone['name']}* удалено!\nСайт обновится на GitHub Pages через ~1 мин.",
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

    logging.info("🍖 Бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
