declare module "pdfobject" {
  interface PDFObjectOptions {
    width?: string;
    height?: string;
    fallbackLink?: string;
    forcePDFJS?: boolean;
    PDFJS_URL?: string;
  }

  const PDFObject: {
    embed: (
      url: string,
      targetSelector: string,
      options?: PDFObjectOptions
    ) => boolean;
  };
  export default PDFObject;
}
