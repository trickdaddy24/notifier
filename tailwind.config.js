/** @type {import('tailwindcss').Config} */
// Self-hosted Tailwind build for the Notifier web dashboard.
// Scans the Jinja templates (including the inline <script> class strings) so
// every utility actually used ends up in web/static/app.css — no Play CDN.
module.exports = {
  content: ["./web/templates/**/*.html"],
  theme: {
    extend: {
      fontFamily: {
        display: ["'Space Grotesk'", "'Inter'", "sans-serif"],
      },
    },
  },
  plugins: [],
};
