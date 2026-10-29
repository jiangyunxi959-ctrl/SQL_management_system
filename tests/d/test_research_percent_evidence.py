"""Research proportions can be displayed as percentages without changing source values."""
import pytest

from app.agent.evidence import check_draft


def _draft(path, raw='0.5'):
    return {'answer':'正IC比例为50% [E1]','citations':['E1'],'claims':[
        {'evidence_id':'E1','path':path,'raw_value':raw,'display_unit':'percent','decimals':1},
    ]}


@pytest.mark.parametrize('tool,data,path', [
    ('evaluation_report',{'headline':{'h1_positive_ratio':'0.5'}},'data.headline.h1_positive_ratio'),
    ('evaluation_report',{'headline':{'h5_positive_ratio':'0.5'}},'headline.h5_positive_ratio'),
    ('evaluation_report',{'summary':{'20':{'positive_ratio':'0.5'}}},'data.summary.20.positive_ratio'),
    ('compare_evaluations',{'left_headline':{'h5_positive_ratio':'0.5'}},'data.left_headline.h5_positive_ratio'),
    ('compare_evaluations',{'right_headline':{'h20_positive_ratio':'0.5'}},'data.right_headline.h20_positive_ratio'),
])
def test_research_ratio_percent_keeps_the_tool_value(tool,data,path):
    assert check_draft(_draft(path),{'E1':{'context':{'tool':tool},'data':data}})[0]


def test_percentage_claim_cannot_replace_decimal_with_scaled_value():
    evidence={'E1':{'context':{'tool':'evaluation_report'},'data':{'headline':{'h5_positive_ratio':'0.5'}}}}
    assert not check_draft(_draft('data.headline.h5_positive_ratio','50'),evidence)[0]


@pytest.mark.parametrize('raw',['-0.1','1.01','NaN','Infinity'])
def test_invalid_research_proportion_is_rejected(raw):
    evidence={'E1':{'context':{'tool':'evaluation_report'},'data':{'headline':{'h5_positive_ratio':raw}}}}
    assert not check_draft(_draft('data.headline.h5_positive_ratio',raw),evidence)[0]


@pytest.mark.parametrize('tool,field',[
    ('factor_statistics','h5_positive_ratio'),
    ('evaluation_report','h2_positive_ratio'),
    ('evaluation_report','h5_mean_ic'),
])
def test_percentage_permission_is_limited_to_declared_research_proportions(tool,field):
    evidence={'E1':{'context':{'tool':tool},'data':{'headline':{field:'0.5'}}}}
    assert not check_draft(_draft('data.headline.'+field),evidence)[0]
