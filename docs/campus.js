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
    return { building: "main", floor: null, room: "БАА", planId: null,
             note: "Большая академическая аудитория — вход из главного (1) корпуса" };
  }
  if (low.includes("спортзал") || low.includes("спортивный зал")) {
    return { building: "k2", floor: 0, room: text, planId: "k2-0",
             note: "Спортзалы № 1–3 — в цокольном этаже 2 корпуса" };
  }
  const m = text.trim().match(/^(\d{3,4})\s*([а-яёa-z])?/i);
  if (!m) return null;
  const d = m[1], room = d + (m[2] || "");
  let building = null, floor = null;
  if (d.length === 3) { building = "main"; floor = +d[0]; }
  else if (d[0] === "1") { building = "k2"; floor = +d[1]; }
  else if (d[0] === "2") { building = "k3"; floor = +d[1]; }
  else if (d[0] === "5") { building = "lib"; floor = +d[1]; }
  if (!building) return null;
  return { building, floor, room, planId: `${building}-${floor}`, note: BUILDINGS[building].note || "" };
}

function floorLabel(floor) {
  if (floor === null || floor === undefined) return "";
  return floor === 0 ? "цокольный этаж" : `${floor} этаж`;
}
