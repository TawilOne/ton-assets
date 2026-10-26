// Корпуса Губкинского университета и разбор номера аудитории.
// Нумерация — по «Краткому путеводителю для первокурсника»:
//   Главный корпус — «этаж + номер»       (348  → 3 этаж)
//   2 корпус       — «1 + этаж + номер»   (1426 → 4 этаж)
//   3 корпус       — «2 + этаж + номер»   (2435 → 4 этаж)
//   Библиотека     — «5 + этаж + номер»   (5810 → 8 этаж)

// язык страницы: бот передаёт его в адресе (?lg=en / fr), по умолчанию русский
const LANG = (() => {
  try { const v = new URLSearchParams(location.search).get("lg"); return ["en", "fr"].includes(v) ? v : "ru"; }
  catch (e) { return "ru"; }
})();
const L3 = (ru, en, fr) => (LANG === "en" ? en : LANG === "fr" ? fr : ru);

const BUILDINGS = {
  main: { name: L3("Главный корпус", "Main building", "Bâtiment principal") },
  k2: { name: L3("2 корпус (лабораторный)", "Building 2 (labs)", "Bâtiment 2 (laboratoires)") },
  k3: { name: L3("3 корпус", "Building 3", "Bâtiment 3"),
        note: L3("Из главного корпуса — через столовую на 2 этаже",
                 "From the main building, go through the canteen on the 2nd floor",
                 "Depuis le bâtiment principal, passez par la cantine au 1er étage") },
  lib: { name: L3("Библиотека", "Library", "Bibliothèque") },
};

// Возвращает {building, floor, room, planId, note} или null.
// planId — имя плана этажа, например "k3-4" (файл plans/k3-4.jpg).
function locateRoom(text) {
  if (!text) return null;
  const low = text.toLowerCase();
  if (low.includes("баа") || low.includes("большая академическая")) {
    return { building: "main", floor: 2, room: "БАА", planId: "main-2",
             note: L3("Большая академическая аудитория — вход из главного (1) корпуса",
                      "Great Academic Hall, entrance from the main building (Building 1)",
                      "Grand amphithéâtre, entrée par le bâtiment principal (bâtiment 1)") };
  }
  if (low.includes("спортзал") || low.includes("спортивный зал")) {
    const g = low.match(/(\d)/);  // «Спортзал №2» -> «Спортзал-2», как на схеме цокольного этажа
    return { building: "k2", floor: 0, room: g ? `Спортзал-${g[1]}` : text, planId: "k2-0",
             note: L3("Спортзалы № 1–3 — в цокольном этаже 2 корпуса",
                      "Gyms 1–3 are on the lower ground floor of Building 2",
                      "Les gymnases 1 à 3 se trouvent au demi-sous-sol du bâtiment 2") };
  }
  const c = text.trim().match(/^ц\s*-?\s*(\d{3}\s*[а-яёa-z]*)/i);  // цокольный этаж 2 корпуса: «Ц117», «Ц107ТБ»
  if (c) return { building: "k2", floor: 0, room: normRoom("Ц" + c[1]), planId: "k2-0", note: "" };
  const m = text.trim().match(/^(\d{3,4})\s*(?:([а-яёa-z])(?![а-яёa-z])|([-/]\s*\d{1,2}))?/i);
  if (!m) return null;
  const d = m[1], room = normRoom(d + (m[2] || m[3] || ""));
  let building = null, floor = null;
  if (d.length === 3) { building = "main"; floor = +d[0]; }
  else if (d[0] === "1") { building = "k2"; floor = +d[1]; }
  else if (d[0] === "2") { building = "k3"; floor = +d[1]; }
  else if (d[0] === "5") { building = "lib"; floor = +d[1]; }
  if (!building) return null;
  return { building, floor, room, planId: `${building}-${floor}`, note: BUILDINGS[building].note || "" };
}

// «803a» (латиница), «803 А», «441 - 2» -> «803А», «441-2» — как ключи в rooms.json
function normRoom(s) {
  const lat = { A: "А", B: "В", E: "Е", C: "С", K: "К", M: "М", H: "Н", O: "О", P: "Р", T: "Т", X: "Х" };
  return String(s || "").toUpperCase().replace(/\s+/g, "").replace(/[ABECKMHOPTX]/g, c => lat[c]);
}

// Где кабинет на плане: точное совпадение, иначе без буквы/дроби («441-2» -> «441»)
function findMark(rooms, room) {
  if (!room) return null;
  const key = normRoom(room);
  return rooms[room] || rooms[key] || rooms[key.replace(/[-/].*$/, "")] || rooms[key.replace(/[А-Я]$/, "")] || null;
}

// этаж по-русски / по-английски (1st floor = уровень земли) / по-французски (1 этаж = rez-de-chaussée)
function floorLabel(floor) {
  if (floor === null || floor === undefined) return "";
  if (floor === 1.5) return L3("между 1 и 2 этажом", "mezzanine (between floors 1 and 2)", "entresol (entre le RDC et le 1er)");
  if (LANG === "en") {
    if (floor === 0) return "lower ground floor";
    const suf = floor % 100 >= 11 && floor % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[floor % 10] || "th");
    return `${floor}${suf} floor`;
  }
  if (LANG === "fr") {
    if (floor === 0) return "demi-sous-sol";
    if (floor === 1) return "rez-de-chaussée";
    return floor === 2 ? "1er étage" : `${floor - 1}e étage`;
  }
  return floor === 0 ? "цокольный этаж" : `${floor} этаж`;
}
