# pyxis-wsrt21cm.py: a Pyxis recipe for WSRT 21cm calibration

## 1. Preliminaries
# All recipes start with this line, which initializes Pyxis
import Pyxis

# import some Pyxides modules.
# Note that Pyxides is implicitly added to the include path
# when you import Pyxis above
import ms,imager,std,lsm,stefcal

# import some other Python modules that we make use of below
import pyfits
import pyrap.tables

## 2. Default variable assignments
# These are variables that control the behaviour of modules.
# Here we just set up some reasonable defaults; config files 
# will almost certainly override them.
# Set up destination directory for all plots, images, etc.
DESTDIR_Template = 'plots-${MS:BASE}${-stage<STAGE}'
# Set up base filename for these files
OUTFILE_Template = '${DESTDIR>/}${MS:BASE}${_s<STEP}${_<LABEL}'

# Extract unpolarized sky models by default
lsm.PYBDSM_POLARIZED = False     
# 0 means we make a single image from all channels. Use 1 to
# make a per-channel cube.
imager.IMAGE_CHANNELIZE = 0  
# Default range of channels to process
ms.CHANRANGE = 2,56,2
# Default set of interferometers to use -- the "standard" 83
# baselines, minus all baselines to RT5 (the APERTIF station)
ms.IFRS = "S83,-5*"
# default place to look for MSs
MS_TARBALL_DIR = os.path.expanduser("~/data")
# default initial sky model
LSM0 = "lsm0.lsm.html"
# default filename for updated sky model
LSM1 = "lsm1.lsm.html"
# default "reference" sky model used to supply dE tags
LSMREF = "lsm-ref.lsm.html"

# this is where we keep track of generated image names
# the use of a Safelist is encouraged, because otherwise parallel jobs might
# trample over the same file
IMAGE_LIST = Safelist("${OUTDIR>/}image.list");

## 2. Procedures
# Procedures are just python functions

def reset_ms ():
  """reset_ms: make a clean start. 
  If FULL_RESET is set (or MS does not exist): does a complete refresh. Removes the measurement set given by the MS variable, 
  untars a fresh copy from the data dir, and does various WSRT-specific initialization.
  If FULL_RESET is not set, then simply clears out flagsets raised during calibration."""
  if is_true("$FULL_RESET") or not exists(MS):
    if exists(MS):
      info("$MS exists but FULL_RESET=1, removing and unpacking fresh MS from tarball");
      x.sh("rm -fr $MS")
    else:
      info("$MS does not exist, unpacking fresh MS from tarball");
    x.sh("cd ${MS:DIR}; tar zxvf ${MS_TARBALL_DIR}/${MS:FILE}.tgz")
    # init MODEL_DATA/CORRECTED_DATA/etc. columns
    pyrap.tables.addImagingColumns(MS)
    # add a bitflag column (used by MeqTrees)
    ms.addbitflagcol();
    # convert UVWs to J2000
    ms.wsrt_j2convert()
    # change the WEIGHT column of redundant baselines, this reduces grating lobes
    # in images
    ms.downweigh_redundant()
    # use the flag-ms.py tool to copy the FLAG column to the "legacy" bitflag
    ms.flagms("-Y +L -f legacy -c")
  else:
    info("$MS exists, reusing (but clearing calibration-related flags). Use FULL_RESET=1 to unpack fresh MS from tarball");
    ms.flagms("-r threshold,fmthreshold,aoflagger,stefcal");

def cal_ms ():
  """cal_ms: runs a calibration loop over the current MS"""
  
  # initialize the calibration "step" counter and "label". These are used 
  # to auto-generate output filenames; cal.stefcal() below automatically increments 
  # cal.STEP
  v.STEP = 0
  # set the superglobal LSM variable. See explanation for "v." in the text
  v.LSM = LSM0
  # info(), warn() and abort() are Pyxis functions for writing output to the log
  info("########## solving for G with initial LSM")
  
  # do one stefcal run with the initial model, produce corrected residuals,
  # do simple flagging on the residuals. See cal.stefcal() for full docs.
  stefcal.stefcal(stefcal_reset_all=True,output="CORR_RES",restore=False,flag_threshold=(1,.5))
  
  # If we got to this point, then assume things are rolling along fine. 
  # Copy the recipe and config to the destination directory for future reference
  # (very useful when modifying recipes, so you can keep track of what settings
  # are responsible for what output!)
  xo.sh("cp pyxis*.py pyxis*.conf ${mqt.TDLCONFIG} $DESTDIR",shell=True)
  
  info("########## remaking data image, running source finder and updating model")
  
  # apply previous stefcal solutions to produce a corrected data image.
  # Make a restored image with 2000 CLEAN iterations.
  stefcal.stefcal(apply_only=True,output="CORR_DATA",plotvis=False,
                  restore=dict(niter=2000),restore_lsm=False)
  
  ## now run pybdsm on the restored image, write output to a new LSM file
  lsm.pybdsm_search(threshold=5,output=LSM1)
  
  # the new sky model becomes the "current" LSM
  v.LSM = LSM1
  
  # note that in principle this step is not needed (may as well go on directly
  # to the dE solutions, below), but it is instructive, as it will produce an intermediate-stage
  # image. But to save time, set restore=False to skip making clean images
  info("########## recalibrating with new model")
  stefcal.stefcal(stefcal_reset_all=True,output="CORR_RES",restore=False,flag_threshold=(1,.5));
  
  # if we have a "reference" sky model configured, use it to set dE tags on
  # sources in our new sky model. Sources with dE tags will have DDE solutions.
  # The cal.transfer_tags() function sets the specified tag on any source
  # near enough to a reference model source with that tag.
  if LSMREF:
    lsm.transfer_tags(LSMREF,LSM,tags="dE",tolerance=45*ARCSEC)
  
    info("########## recalibrating with dEs")
    # re-run stefcal with the new model (and with direction dependent 
    # solutions on dE-tagged sources, if any). Make a corrected residual
    # image, run CLEAN on it, restore the LSM sources back into the
    # image
    stefcal.stefcal(stefcal_reset_all=True,diffgains=True,
        output="CORR_RES",restore=dict(niter=1000),
        restore_lsm=True,flag_threshold=(1,.5))
  # no reference LSM? then still need to make a restored image from the step above
  else:     
    imager.make_image(dirty=False,restore=dict(niter=1000),restore_lsm=True);
      
  # the resulting image filename is in cal.FULLREST_IMAGE,
  # add it to our image list file
  IMAGE_LIST.add("${imager.FULLREST_IMAGE}");
  

def runall ():
  """runall: this runs reset_ms and cal_ms on a bunch of measurement sets""";
  # start a new image list file
  IMAGE_LIST.reset();
  # for every MS in MS_List, run reset_ms() and cal_ms()
  per("MS",reset_ms,cal_ms)
  # make a mean image from every filename accumulated in IMAGE_LIST
  images = IMAGE_LIST.read();
  if images:
    std.fitstool("-m -f -o ${OUTDIR>/}mean-fullrest%d.fits"%len(images),*images)
  