#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高德地图直连异步客户端 - 通过 REST API 直接调用高德服务，不依赖 MCP。

功能:
- POI 搜索: text_search, around_search
- 地理编码: geo, regeocode
- 路线规划: direction_walking, direction_driving, direction_transit_integrated
- 个人地图: schema_personal_map (生成高德小程序二维码)
- IP 定位: ip_location
"""

from __future__ import annotations

import os
import urllib.parse as urlparse
from typing import Any

import httpx

from app.common.config import settings


def _generate_qr_code_url(schema_url: str, size: str = "300x300") -> str:
    qr_service = "https://api.qrserver.com/v1/create-qr-code/"
    return f"{qr_service}?size={size}&data={urlparse.quote(schema_url, safe='')}"


class AMapDirectClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("AMAP_API_KEY") or getattr(settings, "AMAP_API_KEY", None)
        self.api_key_missing = not self.api_key
        self.base_url = "https://restapi.amap.com/v3"
        self.wia_base_url = "https://restapi.amap.com"

    async def _get(self, path: str, params: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
        params["key"] = self.api_key
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(f"{self.base_url}/{path}", params=params)
                return response.json()
        except Exception as exc:
            return {"status": "0", "error": str(exc), "info": f"HTTP请求异常: {exc}"}

    async def _post(self, url: str, params: dict[str, Any], payload: dict[str, Any], timeout: float = 15) -> dict[str, Any]:
        params["key"] = self.api_key
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, params=params, json=payload, headers={"Content-Type": "application/json"})
                return response.json()
        except Exception as exc:
            return {"code": -1, "error": str(exc), "message": f"HTTP请求异常: {exc}"}

    async def text_search(
        self, keywords: str, city: str | None = None, offset: int = 20,
    ) -> list[dict[str, Any]]:
        if self.api_key_missing:
            return [{"error": "amap_key_missing", "message": "AMAP_API_KEY 未配置"}]
        params: dict[str, Any] = {"keywords": keywords, "offset": min(offset, 100), "page": 1}
        if city:
            params["city"] = city
        result = await self._get("place/text", params)
        if result.get("status") != "1":
            return [{"error": "搜索失败", "message": result.get("info", "未知错误")}]
        return self._parse_pois(result.get("pois", []))

    async def around_search(
        self, keywords: str, location: str, radius: int = 1000,
        types: str | None = None, offset: int = 20, page: int = 1,
    ) -> list[dict[str, Any]]:
        if self.api_key_missing:
            return [{"error": "amap_key_missing", "message": "AMAP_API_KEY 未配置"}]
        params: dict[str, Any] = {"keywords": keywords, "location": location, "radius": radius, "offset": min(offset, 100), "page": page}
        if types:
            params["types"] = types
        result = await self._get("place/around", params)
        if result.get("status") != "1":
            return [{"error": "周边搜索失败", "message": result.get("info", "未知错误")}]
        pois = self._parse_pois(result.get("pois", []))
        for item in pois:
            dist = item.get("distance")
            if dist is not None:
                item["distance"] = dist
        return pois

    def _parse_pois(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for poi in raw:
            loc = str(poi.get("location", "")).strip()
            lon, lat = None, None
            if "," in loc:
                try:
                    parts = loc.split(",", 1)
                    lon, lat = float(parts[0]), float(parts[1])
                except (TypeError, ValueError):
                    pass
            out.append({
                "id": poi.get("id", ""),
                "name": poi.get("name", ""),
                "location": {"longitude": lon, "latitude": lat},
                "address": poi.get("address", ""),
                "tel": poi.get("tel", ""),
                "typecode": poi.get("typecode", ""),
            })
        return out

    async def ip_location(self, ip: str) -> dict[str, Any]:
        if self.api_key_missing:
            return {"error": "amap_key_missing"}
        result = await self._get("ip", {"ip": ip})
        if result.get("status") != "1":
            return {"error": "IP定位失败", "message": result.get("info", "未知错误")}
        return {"province": result.get("province", ""), "city": result.get("city", ""), "adcode": result.get("adcode", ""), "rectangle": result.get("rectangle", ""), "location": result.get("loc", ""), "ip": ip}

    async def geo(self, address: str, city: str | None = None) -> dict[str, Any]:
        if self.api_key_missing:
            return {"error": "amap_key_missing"}
        params: dict[str, Any] = {"address": address}
        if city:
            params["city"] = city
        result = await self._get("geocode/geo", params)
        if result.get("status") == "1" and int(result.get("count", 0)) > 0:
            location = result["geocodes"][0]["location"]
            lon, lat = location.split(",")
            return {"longitude": float(lon), "latitude": float(lat), "formatted_address": result["geocodes"][0].get("formatted_address", "")}
        return {"error": "无法找到该地址", "message": result.get("info", "未知错误")}

    async def regeocode(self, longitude: float, latitude: float) -> dict[str, Any]:
        if self.api_key_missing:
            return {"error": "amap_key_missing"}
        params: dict[str, Any] = {"location": f"{longitude},{latitude}", "extensions": "base", "radius": 1000, "batch": "false", "roadlevel": 0}
        result = await self._get("geocode/regeo", params)
        if result.get("status") == "1":
            regeo = result["regeocode"]
            return {"formatted_address": regeo.get("formatted_address", ""), "country": regeo["addressComponent"].get("country", ""), "province": regeo["addressComponent"].get("province", ""), "city": regeo["addressComponent"].get("city", ""), "district": regeo["addressComponent"].get("district", "")}
        return {"error": "逆地理编码失败", "message": result.get("info", "未知错误")}

    async def direction_walking(self, origin: str, destination: str) -> dict[str, Any]:
        if self.api_key_missing:
            return {"error": "amap_key_missing"}
        result = await self._get("direction/walking", {"origin": origin, "destination": destination}, timeout=15)
        if result.get("status") != "1":
            return {"error": "路径规划失败", "message": result.get("info", "未知错误")}
        return result

    async def direction_driving(self, origin: str, destination: str) -> dict[str, Any]:
        if self.api_key_missing:
            return {"error": "amap_key_missing"}
        result = await self._get("direction/driving", {"origin": origin, "destination": destination}, timeout=15)
        if result.get("status") != "1":
            return {"error": "路径规划失败", "message": result.get("info", "未知错误")}
        return result

    async def direction_transit_integrated(self, origin: str, destination: str, city: str = "北京") -> dict[str, Any]:
        if self.api_key_missing:
            return {"error": "amap_key_missing"}
        result = await self._get("direction/transit/integrated", {"origin": origin, "destination": destination, "city": city}, timeout=15)
        if result.get("status") != "1":
            return {"error": "路径规划失败", "message": result.get("info", "未知错误")}
        return result

    async def schema_personal_map(
        self, org_name: str, line_list: list[dict[str, Any]], scene_type: int = 1,
    ) -> dict[str, Any]:
        if self.api_key_missing:
            return {"error": "amap_key_missing", "message": "AMAP_API_KEY 未配置"}
        valid_scene_types = {1, 2, 3}
        if scene_type not in valid_scene_types:
            scene_type = 1
        payload = {"channel": "60000001", "orgName": org_name, "lineList": line_list, "sceneType": scene_type}
        result = await self._post(f"{self.wia_base_url}/rest/wia/mcp/schema", {"source": "personal-map"}, payload)
        if result.get("code") == 1 and result.get("result") is True:
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            schema_url = (
                data.get("schemaUrl")
                or data.get("schema_url")
                or data.get("url")
                or result.get("schemaUrl")
                or result.get("schema_url")
                or ""
            )
            if schema_url:
                return {"qr_code_url": _generate_qr_code_url(schema_url), "schema_url": schema_url, "line_list": line_list, "title": org_name}
            return {"error": "生成地图行程失败", "message": "未返回有效的行程链接"}
        return {"error": "生成地图行程失败", "message": result.get("message") or result.get("info") or "未知错误"}


_amap_direct_client: AMapDirectClient | None = None


def get_amap_direct_client() -> AMapDirectClient:
    global _amap_direct_client
    if _amap_direct_client is None:
        _amap_direct_client = AMapDirectClient()
    return _amap_direct_client
