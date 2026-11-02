//! 标准普通话音节表(约 400,不含声调)+ 拼音切分。
//! 词库 pinyin.key 被整词/长句污染不能用来切分,故切分只用这张干净单音节表(对齐 Python)。

/// 按长度降序的音节表(zh/ch/sh 优先于 z/c/s),切分用。
pub struct Syllables {
    pub ordered: Vec<&'static str>,
}

const SYLLABLE_LIST: &str = "
a ai an ang ao
ba bai ban bang bao bei ben beng bi bian biao bie bin bing bo bu
ca cai can cang cao ce cen ceng cha chai chan chang chao che chen cheng chi chong chou chu chua chuai chuan chuang chui chun chuo ci cong cou cu cuan cui cun cuo
da dai dan dang dao de dei den deng di dia dian diao die ding diu dong dou du duan dui dun duo
e ei en eng er
fa fan fang fei fen feng fo fou fu
ga gai gan gang gao ge gei gen geng gong gou gu gua guai guan guang gui gun guo
ha hai han hang hao he hei hen heng hong hou hu hua huai huan huang hui hun huo
ji jia jian jiang jiao jie jin jing jiong jiu ju juan jue jun
ka kai kan kang kao ke kei ken keng kong kou ku kua kuai kuan kuang kui kun kuo
la lai lan lang lao le lei leng li lia lian liang liao lie lin ling liu long lou lu lv luan lve lun luo
ma mai man mang mao me mei men meng mi mian miao mie min ming miu mo mou mu
na nai nan nang nao ne nei nen neng ni nian niang niao nie nin ning niu nong nou nu nv nuan nve nun nuo
o ou
pa pai pan pang pao pei pen peng pi pian piao pie pin ping po pou pu
qi qia qian qiang qiao qie qin qing qiong qiu qu quan que qun
ran rang rao re ren reng ri rong rou ru ruan rui run ruo
sa sai san sang sao se sen seng sha shai shan shang shao she shei shen sheng shi shou shu shua shuai shuan shuang shui shun shuo si song sou su suan sui sun suo
ta tai tan tang tao te teng ti tian tiao tie ting tong tou tu tuan tui tun tuo
wa wai wan wang wei wen weng wo wu
xi xia xian xiang xiao xie xin xing xiong xiu xu xuan xue xun
ya yan yang yao ye yi yin ying yo yong you yu yuan yue yun
za zai zan zang zao ze zei zen zeng zha zhai zhan zhang zhao zhe zhei zhen zheng zhi zhong zhou zhu zhua zhuai zhuan zhuang zhui zhun zhuo zi zong zou zu zuan zui zun zuo
";

impl Syllables {
    pub fn new() -> Self {
        let mut v: Vec<&'static str> = SYLLABLE_LIST.split_whitespace().collect();
        v.sort_by(|a, b| b.len().cmp(&a.len())); // 长度降序
        Syllables { ordered: v }
    }

    /// 拼音切分:连续拼音串 → 有效音节序列。"nihao" -> ["ni","hao"]。
    ///
    /// 2026-11-01 修复:原实现是纯贪心最长匹配 + 匹配不上就跳单字符,
    /// 遇到「前音节以 n/ng 结尾、后音节以 g 开头」的边界会吃掉后音节的声母 g,
    /// 再把残留的 "ong" 当成音节 "o",尾巴 "ng" 被静默丢弃。
    /// 例:mingong(民工) 被切成 ["ming","o"],rengong(人工) 被切成 ["reng","o"],
    /// 拼出的 key 不存在于词库,导致整句识别退化成逐词瞎拼。
    ///
    /// 现在先尝试「完整消费」的回溯切分;整串无法完整切分时(简拼/混拼输入),
    /// 才退回原来的贪心跳字符行为,保持旧语义不变。
    pub fn segment(&self, input: &str) -> Vec<String> {
        if let Some(v) = self.segment_full(input) {
            return v;
        }
        self.segment_greedy(input)
    }

    /// 带回溯的完整切分:要求整串被音节表恰好消费完,否则返回 None。
    /// can[i] = 从第 i 字节起能否完整切分;先反向 DP 求可达性,再正向重建。
    fn segment_full(&self, input: &str) -> Option<Vec<String>> {
        let bytes = input.as_bytes();
        let n = bytes.len();
        if n == 0 {
            return Some(Vec::new());
        }
        let mut can = vec![false; n + 1];
        can[n] = true;
        for i in (0..n).rev() {
            for key in &self.ordered {
                let kb = key.as_bytes();
                if i + kb.len() <= n && &bytes[i..i + kb.len()] == kb && can[i + kb.len()] {
                    can[i] = true;
                    break;
                }
            }
        }
        if !can[0] {
            return None;
        }
        let mut out = Vec::new();
        let mut i = 0;
        while i < n {
            let mut advanced = false;
            for key in &self.ordered {
                let kb = key.as_bytes();
                if i + kb.len() <= n && &bytes[i..i + kb.len()] == kb && can[i + kb.len()] {
                    out.push(key.to_string());
                    i += kb.len();
                    advanced = true;
                    break;
                }
            }
            if !advanced {
                return None; // 理论不可达(can[0] 已保证),保险起见退回贪心
            }
        }
        Some(out)
    }

    /// 旧行为:贪心最长匹配,匹配不上跳单字符。保留给简拼/混拼输入。
    fn segment_greedy(&self, input: &str) -> Vec<String> {
        let bytes = input.as_bytes();
        let mut result = Vec::new();
        let mut i = 0;
        while i < bytes.len() {
            let mut matched = false;
            for key in &self.ordered {
                let kb = key.as_bytes();
                if i + kb.len() <= bytes.len() && &bytes[i..i + kb.len()] == kb {
                    result.push(key.to_string());
                    i += kb.len();
                    matched = true;
                    break;
                }
            }
            if !matched {
                i += 1;
            }
        }
        result
    }

    /// s 能否完整切成音节(整串都是全拼,无简拼首字母)。
    pub fn is_full_pinyin(&self, s: &str) -> bool {
        if s.is_empty() {
            return false;
        }
        let segs = self.segment(s);
        !segs.is_empty() && segs.concat().len() == s.len()
    }
}
