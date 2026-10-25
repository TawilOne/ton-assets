// Cloudflare Worker: пересылает запросы бота в Telegram Bot API.
// Бот обращается к https://ИМЯ.workers.dev/bot<токен>/метод, а Worker передаёт это в api.telegram.org.
// Пропускает только адреса Bot API, чтобы Worker нельзя было использовать как прокси на другие сайты.
export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/bot") && !url.pathname.startsWith("/file/bot")) {
      return new Response("ok");
    }
    return fetch(new Request("https://api.telegram.org" + url.pathname + url.search, request));
  },
};
