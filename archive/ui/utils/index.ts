import { PointOptionsObject, SeriesPieOptions } from "highcharts";
import { Scheme } from "~/definitions/mutualfunds";
import {
  mainPieData,
  drillDownPieData,
  mainLevelDrillDownData,
} from "~/definitions/charts";

export interface AllocationPieChartData {
  series: Array<PointOptionsObject>;
  drilldown: Array<SeriesPieOptions>;
}

export const preparePieChartData = (
  schemes: Array<Scheme>,
  totalValue: number
): AllocationPieChartData => {
  const mainPie: mainPieData = {};
  const drillDownPie: drillDownPieData = {};
  const mainLevelDrillDownPie: mainLevelDrillDownData = {};

  schemes.forEach(({ name, category: { main, sub }, value }) => {
    if (!Object.prototype.hasOwnProperty.call(mainPie, main)) {
      mainPie[main] = { name: main, y: 0.0, drilldown: main };
    }
    if (!Object.prototype.hasOwnProperty.call(drillDownPie, sub)) {
      drillDownPie[sub] = { name: sub, id: sub, data: [], type: "pie" };
    }
    if (!Object.prototype.hasOwnProperty.call(mainLevelDrillDownPie, main)) {
      mainLevelDrillDownPie[main] = {};
    }
    if (
      !Object.prototype.hasOwnProperty.call(mainLevelDrillDownPie[main], sub)
    ) {
      mainLevelDrillDownPie[main][sub] = { name: sub, y: 0.0, drilldown: sub };
    }
    const pct =
      Math.round((10000.0 * value + Number.EPSILON) / totalValue) / 100;
    drillDownPie[sub]!.data!.push({ name, y: pct });
    mainLevelDrillDownPie[main][sub]!.y! += pct;
    mainPie[main]!.y! += pct;
  });
  Object.entries(mainLevelDrillDownPie).forEach(([key, val]) => {
    drillDownPie[key] = {
      name: key,
      id: key,
      data: Object.values(val),
      type: "pie",
    };
  });
  return {
    series: Object.values(mainPie),
    drilldown: Object.values(drillDownPie),
  };
};
