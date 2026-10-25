"""Natural Earth admin-1 polygons -> data/admin1-world.tsv, the country and region of a station.

Run offline, with geopandas and shapely, on ne_10m_admin_1_states_provinces.shp
(https://www.naturalearthdata.com/, public domain):

    python3 build_admin1.py ne_10m_admin_1_states_provinces.shp admin1-world.tsv

One line per outer ring: ISO country code, region, then "lon,lat" pairs to 4
decimals, tab-separated from the region and space-separated from each other.
Holes are left out: a point inside an enclave (San Marino in Italy) falls in
two rings, and the reader takes the smaller. Rings are simplified to 0.002
degrees, about 200 m, which keeps the file small and moves a border by less
than a station on a ridge needs to be on the wrong side.

The region is the first-level unit, except where Natural Earth's first level
is finer than the one people use: the 110 Italian provinces are grouped into
the 20 regions, French departments, Spanish provinces and Slovenian
municipalities likewise (the `region` field), and the United Kingdom is split
into its four nations (`geonunit`).
"""
import sys

import geopandas as gpd

GROUPED = {"IT", "FR", "ES", "SI"}
SIMPLIFY_DEG = 0.002


def region_of(row):
    if row.iso_a2 in GROUPED and row.region:
        return row.region
    if row.iso_a2 == "GB" and row.geonunit:
        return row.geonunit
    return row["name"] or ""


def main(src, out):
    g = gpd.read_file(src, columns=["iso_a2", "name", "region", "geonunit"])
    g[["region", "geonunit", "name"]] = g[["region", "geonunit", "name"]].fillna("")
    g["cc"] = g.iso_a2.where(g.iso_a2 != "-99", "")
    g["reg"] = [region_of(r).replace("\t", " ") for _, r in g.iterrows()]
    # One shape per country and region: the borders between provinces of one
    # region carry no information here and were most of the points.
    g = g.dissolve(by=["cc", "reg"], as_index=False)
    rings = 0
    with open(out, "w", encoding="utf-8") as f:
        f.write("# country\tregion\tring (lon,lat ...), Natural Earth 10m admin-1, simplified to %s deg\n" % SIMPLIFY_DEG)
        for _, row in g.iterrows():
            cc, region = row.cc, row.reg
            geom = row.geometry.simplify(SIMPLIFY_DEG, preserve_topology=True)
            polys = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
            for p in polys:
                coords = list(p.exterior.coords)
                if len(coords) < 4:
                    continue
                f.write("%s\t%s\t%s\n" % (cc, region, " ".join("%.4f,%.4f" % (x, y) for x, y in coords)))
                rings += 1
    print(out, rings, "rings")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
