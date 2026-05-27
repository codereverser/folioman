import { Options, PointOptionsObject, SeriesPieOptions } from "highcharts";

export interface Chart {
  showLoading(): void;
  hideLoading(): void;
  update(options: Options, redraw?: boolean, oneToOne?: boolean): void;
  reflow(): void;
}

export interface mainPieData {
  [key: string]: PointOptionsObject;
}

export interface drillDownPieData {
  [key: string]: SeriesPieOptions;
}

export interface mainLevelDrillDownData {
  [key: string]: mainPieData;
}
