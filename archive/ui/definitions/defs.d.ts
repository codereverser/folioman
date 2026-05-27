import { PDFData } from "~/definitions/casparser";

export interface StepEvent extends Event {
  pageIndex: number;
  pdfData?: PDFData | null;
  uploadStatus?: boolean;
}

export interface ImportData {
  pdfData: PDFData | null;
}
