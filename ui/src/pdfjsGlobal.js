// pdf_viewer.mjs reads PDF.js from `globalThis.pdfjsLib` when it is evaluated, so
// this module has to run first: PdfReader imports it before the viewer.
import * as pdfjsLib from "pdfjs-dist";

globalThis.pdfjsLib = pdfjsLib;
export default pdfjsLib;
