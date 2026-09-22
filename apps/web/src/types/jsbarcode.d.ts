declare module "jsbarcode" {
  type Options = {
    format?: string; width?: number; height?: number; displayValue?: boolean; fontSize?: number;
    margin?: number; background?: string; lineColor?: string; font?: string; textMargin?: number;
  };
  export default function JsBarcode(element: SVGElement | string, value: string, options?: Options): void;
}
