// Корпуса Губкинского университета и разбор номера аудитории.
// Нумерация — по «Краткому путеводителю для первокурсника»:
//   Главный корпус — «этаж + номер»       (348  → 3 этаж)
//   2 корпус       — «1 + этаж + номер»   (1426 → 4 этаж)
//   3 корпус       — «2 + этаж + номер»   (2435 → 4 этаж)
//   Библиотека     — «5 + этаж + номер»   (5810 → 8 этаж)

const BUILDINGS = {
  main: { name: "Главный корпус" },
  k2: { name: "2 корпус (лабораторный)" },
  k3: { name: "3 корпус", note: "Из главного корпуса — через столовую на 2 этаже" },
  lib: { name: "Библиотека" },
};

// Возвращает {building, floor, room, planId, note} или null.
// planId — имя плана этажа, например "k3-4" (файл plans/k3-4.jpg).
function locateRoom(text) {
  if (!text) return null;
  const low = text.toLowerCase();
  if (low.includes("баа") || low.includes("большая академическая")) {
    return { building: "main", floor: 2, room: "БАА", planId: "main-2",
             note: "Большая академическая аудитория — вход из главного (1) корпуса" };
  }
  if (low.includes("спортзал") || low.includes("спортивный зал")) {
    return { building: "k2", floor: 0, room: text, planId: "k2-0",
             note: "Спортзалы № 1–3 — в цокольном этаже 2 корпуса" };
  }
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
  return rooms[key] || rooms[key.replace(/[-/].*$/, "")] || rooms[key.replace(/[А-Я]$/, "")] || null;
}

function floorLabel(floor) {
  if (floor === null || floor === undefined) return "";
  return floor === 0 ? "цокольный этаж" : `${floor} этаж`;
}
