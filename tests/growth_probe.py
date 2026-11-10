"""Real isolated compiler check for local/container verification, no model needed."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/astrbot_plugin_kouhu_oj'))
from growth_validation import validate_candidate
CANDIDATE={'id':'probe','category':'STL','level':1,'title':'vector构造','question':'目标是构造包含三个2的vector。哪里不对？','buggy_code':'std::vector<int> v{3, 2};','answer':'花括号使用initializer_list得到两个元素，圆括号才是个数和值。','fix':'std::vector<int> v(3, 2);','cpp_standard':'c++20','validation':{'buggy_program':'#include <vector>\n#include <iostream>\nint main(){std::vector<int> v{3, 2};std::cout<<v.size();}','fixed_program':'#include <vector>\n#include <iostream>\nint main(){std::vector<int> v(3, 2);std::cout<<v.size();}','stdin':'','expected_stdout':'3','buggy_kind':'wrong_output'}}
if __name__=='__main__':
    result=validate_candidate(CANDIDATE)
    print(json.dumps(result,ensure_ascii=False))
    if not result['ok']: raise SystemExit(1)
