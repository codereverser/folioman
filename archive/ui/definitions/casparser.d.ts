/* eslint-disable camelcase */
interface StatementPeriod {
  from: string;
  to: string;
}

interface InvestorInfo {
  name: string;
  address: string;
  email: string;
  mobile: string;
}

interface Transaction {
  date: string;
  description: string;
  amount: number;
  nav: number;
  units: number;
  balance: number;
}

interface Scheme {
  scheme: string;
  rta: string;
  rta_code: string;
  advisor: string;
  open: number;
  close: number;
  transactions: Array<Transaction>;
}

interface Folio {
  folio: string;
  PAN: string;
  KYC: string;
  PANKYC: string;
  schemes: Array<Scheme>;
}

interface Folios {
  [key: string]: Folio;
}

export interface PDFData {
  file_type: string;
  statement_period: StatementPeriod;
  investor_info: InvestorInfo;
  folios: Folios;
}
