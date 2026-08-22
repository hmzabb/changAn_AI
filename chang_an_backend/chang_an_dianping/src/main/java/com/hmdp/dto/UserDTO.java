package com.hmdp.dto;

import lombok.Data;

import java.util.HashMap;
import java.util.Map;

@Data
public class UserDTO {
    private Long id;
    private String nickName;
    private String icon;

    public Map<String, Object> toMap(){
        Map<String, Object> map = new HashMap<>();
        String idStr = String.valueOf(id);
        map.put("id", idStr);
        map.put("nickName", this.nickName);
        map.put("icon", this.icon);
        return map;
    }

}
