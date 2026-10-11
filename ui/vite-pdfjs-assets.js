// PDF.js loads fonts, character maps and image decoders from URLs at run time.
// Serve them from node_modules while developing and copy them into the build, so
// a PDF that does not embed its fonts, or a scan, renders properly.
import fs from "node:fs";
import path from "node:path";

const DIRS = ["standard_fonts", "cmaps", "wasm"];

export default function pdfjsAssets(root = path.resolve("node_modules/pdfjs-dist")) {
  return {
    name: "pdfjs-assets",
    configureServer(server) {
      server.middlewares.use("/pdfjs", (request, response, next) => {
        const file = path.join(root, decodeURIComponent((request.url || "").split("?")[0]));
        const inside = file.startsWith(root + path.sep) && DIRS.includes(path.relative(root, file).split(path.sep)[0]);
        if (!inside || !fs.existsSync(file) || fs.statSync(file).isDirectory()) return next();
        response.setHeader("Content-Type", "application/octet-stream");
        fs.createReadStream(file).pipe(response);
      });
    },
    generateBundle() {
      for (const dir of DIRS) {
        for (const name of fs.readdirSync(path.join(root, dir))) {
          this.emitFile({ type: "asset", fileName: `pdfjs/${dir}/${name}`, source: fs.readFileSync(path.join(root, dir, name)) });
        }
      }
    },
  };
}
