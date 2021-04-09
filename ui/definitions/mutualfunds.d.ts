export interface MFPortfolio {
  id: number;
  name: string;
  email: string;
  pan: string;
}

export interface SchemeCategory {
  main: string;
  sub: string;
}
export interface Folio {
  folio: String;
  invested: number;
  units: number;
  value: number;
  // eslint-disable-next-line camelcase
  avg_nav: number;
}

export interface Scheme {
  name: string;
  category: SchemeCategory;
  nav0: number;
  nav1: number;
  folios: Array<Folio>;
  invested: number;
  units: number;
  value: number;
  // eslint-disable-next-line camelcase
  avg_nav: number;
}
