"use client";

// Last resort, when even the locale layout has failed, so translations aren't available.
const MESSAGE = "Something went wrong. Please reload the page.";

export default function GlobalError() {
  return (
    <html lang="en">
      <body>
        <h1>{MESSAGE}</h1>
      </body>
    </html>
  );
}
